"""Smoke tests for GET /v1/bien/logements (D-12/T-48-03/D-16, Phase 51 D-18).

No real DB is opened: the fetches used by the router are monkeypatched with
fabricated rows, and ``get_db`` is overridden with a throwaway session that the
patched fetches never touch (pattern ``tests/test_health.py``).

Scope split with ``tests/test_logements_rattachement.py``: that file owns the
Phase 51 attribution boundary itself (which state, which cards, which
aggregate). This one owns the surface AROUND it -- auth gate, parameter
validation, the Phase 48 verdict computed on top of the decision, the response
shape and the confinement of monetary fields to the sale rows (D-17).

The attachment cascade this file used to describe (exact key -> widened RNB keys
-> 12 m spatial fallback, all three feeding the cards) no longer exists: Plan
51-03 replaced it with the single policy shared with ``/v1/resolve``. The tests
that pinned it were not merely rewritten, they were INVERTED -- several of them
asserted, as desirable, that geometry may publish a card. What that cost in
coverage is named out loud in :class:`TestCleOrpheline` rather than hidden.
"""

import json
from datetime import date
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.smoke
from httpx import ASGITransport, AsyncClient

from app.database import get_db
from app.dpe.inventory_service import DpeInventoryServiceResult
from app.dpe.schemas import (
    DpeInventoryDistribution,
    DpeInventoryItem,
    DpeInventoryPage,
)
from app.main import app


@pytest.fixture()
async def public_client():
    """Client without auth override (for the 401/403 proof, T-48-03)."""
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _fake_db_session():
    """Override get_db with a throwaway session (never touched, queries are mocked)."""

    async def _fake_db():
        return AsyncMock()

    app.dependency_overrides[get_db] = _fake_db
    yield
    app.dependency_overrides.pop(get_db, None)


ID_PARCELLE = "85047000AB0042"
CLE_RESOLUE = "44109_3912_00001"
# Other entrance of the SAME RNB building (corner building, R4-02).
CLE_ANGLE = "44109_3056_00012"


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
    """Fabricate one DPE row (columns of the two attribution fetches)."""
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


def _copro_row(lots: int = 8) -> dict:
    return {
        "numero_immatriculation": "AB1234567",
        "nom_copropriete": "Residence Test",
        "nb_lots_total": 10,
        "nb_lots_habitation_strict": lots,
        "periode_construction": "1980-1989",
    }


def _dvf_row() -> dict:
    """One row as ``dvf_appartements_par_parcelle`` projects it since D-17.

    Mutation grain, not DVF line: the amount is the whole mutation's, and it
    travels with the class that says what was sold. Mono-local here, so the
    price per m2 exists and carries no absence reason.
    """
    return {
        "date_mutation": date(2024, 3, 1),
        "type_local": "Appartement",
        "surface_reelle_bati": 65,
        "nombre_pieces_principales": 3,
        "valeur_fonciere": 250000.0,
        "classe_mutation": "residentiel_appartement",
        "mono_local": True,
        "hors_stats_vefa": False,
        "prix_m2": 3846.0,
        "prix_m2_raison": None,
        "nb_locaux_residentiel": 1,
        "nb_locaux_commercial": 0,
    }


def _patch_fetches(
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


class TestAuthGate:
    async def test_requires_api_key(self, public_client):
        """T-48-03: the endpoint is under check_quota_hybrid, same as resolve."""
        resp = await public_client.get(
            "/v1/bien/logements", params={"id_parcelle": ID_PARCELLE}
        )
        assert resp.status_code in (401, 403)


class TestValidation:
    async def test_rejects_short_id_parcelle(self, client):
        resp = await client.get("/v1/bien/logements", params={"id_parcelle": "8504700"})
        assert resp.status_code == 422

    async def test_rejects_invalid_charset_id_parcelle(self, client):
        resp = await client.get(
            "/v1/bien/logements", params={"id_parcelle": "85047000ab0042"}
        )
        assert resp.status_code == 422

    async def test_rejects_missing_id_parcelle(self, client):
        resp = await client.get("/v1/bien/logements")
        assert resp.status_code == 422

    async def test_limite_hors_bornes(self, client):
        resp_bas = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "limite": 0},
        )
        assert resp_bas.status_code == 422

        resp_haut = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "limite": 999},
        )
        assert resp_haut.status_code == 422

    async def test_rejette_un_ban_id_malforme(self, client):
        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": "44109 0760;DROP"},
        )
        assert resp.status_code == 422


class TestIdentiteExacte:
    """RESEARCH-L1 step 2 / Phase 51 D-04: the BAN key is the ONLY path."""

    async def test_la_cle_exacte_rattache_les_dpe_que_le_spatial_ratait(
        self, client, monkeypatch
    ):
        """2 rue des Essais in miniature: 0 row within the parcel geometry, 2 under
        the exact key -- the reason the key path exists at all."""
        _patch_fetches(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-ban-1", etage=2, complement="Porte 21"),
                _dpe_row("dpe-ban-2", etage=3, complement="Porte 31"),
            ],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "unassigned"
        assert donnees["dpe_attribution"]["reason"] == "dwelling_identity_unproven"
        assert donnees["diagnostics_charges"] == 2
        assert "logements" not in donnees
        # Detection reads the SAME decision: signal D2 can never diverge from
        # the published list.
        assert donnees["detection"]["signaux"]["dpe_appart"]["compte"] == 2
        assert donnees["detection"]["signaux"]["dpe_appart"]["positif"] is True

    async def test_sans_cle_resolue_rien_n_est_interroge(self, client, monkeypatch):
        """No BAN key -> typed absence, and not a single DPE read.

        This is the case the removed cascade answered with the unrestricted
        spatial path, i.e. with the parcel's other building.
        """
        _patch_fetches(monkeypatch, exact_rows=[_dpe_row("jamais-charge")])

        resp = await client.get(
            "/v1/bien/logements", params={"id_parcelle": ID_PARCELLE}
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "absent"
        assert donnees["dpe_attribution"]["reason"] == "identity_unavailable"
        assert donnees["diagnostics_charges"] == 0

        from app.logements import router as logements_router

        assert (
            logements_router.load_dpe_inventory_result.await_args.kwargs["ban_id"]
            is None
        )
        logements_router.cles_ban_batiment_rnb.assert_not_awaited()

    async def test_les_cles_rnb_alimentent_le_fetch_batiment_pas_les_cartes(
        self, client, monkeypatch
    ):
        """R4-02 kept, demoted: the corner building's other entrance is read
        through its OWN fetch and can only ever feed the aggregate."""
        _patch_fetches(
            monkeypatch,
            exact_rows=[],
            batiment_rows=[_dpe_row("dpe-angle", identifiant_ban=CLE_ANGLE)],
            rnb_cles=(CLE_RESOLUE, CLE_ANGLE),
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "absent"
        assert donnees["dpe_attribution"]["reason"] == "not_reported"
        assert donnees["dpe_attribution"]["building_context"]["count"] == 1
        assert "logements" not in donnees

        from app.logements import router as logements_router

        cles = list(logements_router.dpe_par_identifiants_ban.await_args.args[1])
        assert cles == [CLE_ANGLE], cles
        # Shared budget: the building fetch keeps the SAME limite bound.
        assert (
            logements_router.dpe_par_identifiants_ban.await_args.kwargs["limit"] == 120
        )

    async def test_rnb_liens_sans_porteur_reste_sur_la_cle_seule(
        self, client, monkeypatch
    ):
        """No carrier in rnb_liens: exact single-key answer, HTTP 200.

        A carrier lookup returning no row proves that widening degrades to the
        strict exact-BAN inventory instead of emptying the fiche.
        """
        from app.logements import router as logements_router

        _patch_fetches(
            monkeypatch,
            exact_rows=[_dpe_row("dpe-ban-1", etage=2, complement="Porte 21")],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "unassigned"
        assert donnees["diagnostics_charges"] == 1
        logements_router.dpe_par_identifiants_ban.assert_not_awaited()

    async def test_adresse_postale_complete_ne_decide_rien(self, client, monkeypatch):
        """The T4 premise, re-locked under D-18: ``adresse`` decides nothing.

        It never could separate two street numbers (``diagnostics_dpe`` carries
        no postal address field), and since Phase 51 it does not narrow the DPE
        decision at all -- the identity does. Passing it must therefore leave
        the payload byte-identical.
        """
        rows = [
            _dpe_row("dpe-a", etage=2, complement="Bat A Porte 21"),
            _dpe_row("dpe-b", etage=1, complement="Bat B Porte 11"),
        ]
        params = {"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE}

        _patch_fetches(monkeypatch, exact_rows=rows)
        sans = await client.get("/v1/bien/logements", params=params)
        _patch_fetches(monkeypatch, exact_rows=rows)
        avec = await client.get(
            "/v1/bien/logements", params={**params, "adresse": "Bat A"}
        )

        assert sans.status_code == avec.status_code == 200
        assert sans.json()["donnees"] == avec.json()["donnees"]
        assert "logements" not in sans.json()["donnees"]


class TestCleOrpheline:
    """The measured cost of the exact boundary, stated rather than hidden.

    An ORPHAN key -- the DPE rows of the right address carry a different FANTOIR
    code than the one ``/v1/resolve`` returns (8,7 % of dept 44, 20 % of dept 85,
    finding R3-04) -- was previously rescued by the 12 m spatial fallback. It no
    longer is: no identity match, no attribution.

    That is the deliberate trade of this phase. The fallback rescued those
    fiches by publishing whatever stood within 12 m, which on a shared parcel is
    the NEIGHBOUR's dwellings (regression #46/#47/#48, three corrective PR in
    24 h). An honest ``absent`` is the answer; closing the orphan gap is an
    identity problem, to be solved on the key, not on geometry.
    """

    async def test_une_cle_orpheline_donne_une_absence_honnete(
        self, client, monkeypatch
    ):
        _patch_fetches(monkeypatch, exact_rows=[], rnb_cles=())

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "absent"
        assert donnees["dpe_attribution"]["reason"] == "not_reported"
        assert "logements" not in donnees
        assert donnees["rattachement"] == {
            "exact": 0,
            "elargi_batiment": 0,
            "spatial": 0,
        }


class TestReplitDeuxSignauxSansCarte:
    async def test_repli_deux_signaux_sans_carte(self, client, monkeypatch):
        """B2/D-03: copro + 3 opaque DPE (no card) + 0 DVF -> S4, 'immeuble'.

        Opaque terminals cannot be told apart, so the policy answers
        ``building/ambiguous_unresolved`` -- diagnostics DO exist here, none is
        attributable. The fiche must still say « immeuble » on 2 signals.
        """
        _patch_fetches(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-1", complement="N/A"),
                _dpe_row("dpe-2", complement="N/A"),
                _dpe_row("dpe-3", complement="N/A"),
            ],
            copro_rows=[_copro_row()],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["dpe_attribution"]["status"] == "unassigned"
        assert donnees["dpe_attribution"]["reason"] == "dwelling_identity_unproven"
        assert donnees["detection"]["mode"] == "immeuble"
        assert "logements" not in donnees
        assert donnees["denominateur"]["non_distinguables"] == 0


class TestPromotionIdentites:
    """ADR-0006: DPE-only proof by distinct identities -- and its hard limit."""

    async def test_guerande_promu_immeuble_par_ses_seuls_dpe(self, client, monkeypatch):
        """1 rue de Guerande in miniature: 3 rows, 0 copro, 0 DVF -> immeuble.

        Two of the three rows are the same re-surveyed appartement. Since Phase
        51 that is stated by the ADEME replacement edge, not inferred from a
        surface/date clustering: ``dpe-guerande-2`` declares it replaces
        ``dpe-guerande-1``, so only 2 terminals remain. 2 distinct identities,
        one carrying a porte token -> promotion, auditable via
        ``preuve_identites``.
        """
        _patch_fetches(
            monkeypatch,
            exact_rows=[
                _dpe_row(
                    "dpe-guerande-1",
                    etage=2,
                    complement="Appartement au 2eme etage porte de droite",
                    surface=44.7,
                    classe="E",
                ),
                _dpe_row(
                    "dpe-guerande-2",
                    etage=2,
                    complement="Appartement au 2eme etage porte de droite",
                    surface=44.7,
                    classe="D",
                    remplace="dpe-guerande-1",
                ),
                _dpe_row(
                    "dpe-guerande-3",
                    etage=0,
                    complement="Etage 4 porte 2",
                    surface=29.6,
                    classe="F",
                ),
            ],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["detection"]["mode"] == "indice"
        assert donnees["detection"]["signaux_positifs"] == 1
        assert donnees["detection"]["preuve_identites"] is False
        assert "logements" not in donnees
        assert "dpe-guerande-1" not in resp.text, "the replaced survey is not a card"

    async def test_maison_multi_etages_jamais_promue(self, client, monkeypatch):
        """Hard gate (D-02, review T3/T6): a mislabelled maison whose two
        surveys cite different floors -- no porte/batiment token -- stays
        'indice'. Re-survey != immeuble."""
        _patch_fetches(
            monkeypatch,
            exact_rows=[
                _dpe_row("dpe-maison-1", etage=1, surface=95.0),
                _dpe_row("dpe-maison-2", etage=2, surface=102.0),
            ],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        assert donnees["detection"]["mode"] == "indice"
        assert donnees["detection"]["preuve_identites"] is False


class TestContractShapeOnSuccess:
    async def test_contract_shape_on_success(self, client, monkeypatch):
        _patch_fetches(
            monkeypatch,
            exact_rows=[_dpe_row("dpe-1", etage=2, complement="Bat A Porte 12")],
            dvf_rows=[_dvf_row()],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        body = resp.json()
        donnees = body["donnees"]
        assert {
            "detection",
            "denominateur",
            "ventes_immeuble",
            "dpe_attribution",
        } <= set(donnees)
        # Phase 01-04: `logements` was removed, not emptied. It must not come
        # back unnoticed under the shape check that used to require it.
        assert "logements" not in donnees
        assert isinstance(body["meta"]["source"], list)
        assert len(body["meta"]["source"]) == 3


class TestInventoryV2Integration:
    async def test_complete_apartment_count_never_creates_dwellings(
        self,
        client,
        monkeypatch,
    ):
        _patch_fetches(
            monkeypatch,
            exact_rows=[
                _dpe_row("apartment-1", type_batiment="appartement"),
                _dpe_row("house-1", type_batiment="maison"),
                _dpe_row("opaque-1", type_batiment=""),
                _dpe_row("apartment-2", type_batiment=" appartement "),
            ],
        )

        response = await client.get(
            "/v1/bien/logements",
            params={
                "id_parcelle": ID_PARCELLE,
                "ban_id": CLE_RESOLUE,
                "limite": 200,
            },
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        data = response.json()["donnees"]
        assert data["dpe_inventory"]["total"] == 4
        assert data["detection"]["signaux"]["dpe_appart"]["compte"] == 2
        assert "logements" not in data
        assert data["detection"]["preuve_identites"] is False
        assert data["denominateur"]["logements_connus"] == 0
        assert data["denominateur"]["non_distinguables"] == 0

        from app.logements import router as logements_router

        assert (
            logements_router.load_dpe_inventory_result.await_args.kwargs["limit"] == 20
        )

    async def test_v2_keeps_private_source_fields_outside_the_historical_payload(
        self,
        client,
        monkeypatch,
    ):
        private_values = (
            "DPE-ADEME-PRIVE-58108",
            "DPE-REMPLACE-PRIVE-58108",
            "Batiment secret porte 97 etage 41",
            "17 impasse source privee 44999 Villetest",
            "47.998811",
            "-1.887722",
            CLE_RESOLUE,
        )
        private_row = _dpe_row(
            private_values[0],
            etage=41,
            complement=private_values[2],
            remplace=private_values[1],
        )
        private_row.update(
            {
                "adresse_brute": private_values[3],
                "latitude": 47.998811,
                "longitude": -1.887722,
            }
        )
        _patch_fetches(
            monkeypatch,
            exact_rows=[private_row],
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )

        response = await client.get(
            "/v1/bien/logements",
            params={
                "id_parcelle": ID_PARCELLE,
                "ban_id": CLE_RESOLUE,
                "adresse": private_values[3],
            },
        )

        assert response.status_code == 200
        data = response.json()["donnees"]
        assert data["dpe_inventory"]["version"] == 2
        assert data["dpe_inventory"]["state"] == "ready"
        assert data["dpe_inventory"]["total"] == 1
        assert data["dpe_attribution"]["status"] == "unassigned"
        assert data["denominateur"]["lots_declares"] == 8
        assert len(data["ventes_immeuble"]) == 1
        serialized = json.dumps(response.json(), ensure_ascii=False)
        for private_value in private_values:
            assert private_value not in serialized

    async def test_rnb_failure_keeps_inventory_copro_and_dvf(
        self,
        client,
        monkeypatch,
        caplog,
    ):
        _patch_fetches(
            monkeypatch,
            exact_rows=[_dpe_row("exact-private")],
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )
        monkeypatch.setattr(
            "app.logements.router.cles_ban_batiment_rnb",
            AsyncMock(side_effect=RuntimeError("private-rnb-detail")),
        )

        response = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert response.status_code == 200
        data = response.json()["donnees"]
        assert data["dpe_inventory"]["state"] == "ready"
        assert data["dpe_inventory"]["total"] == 1
        assert data["dpe_attribution"]["status"] == "unassigned"
        assert data["denominateur"]["lots_declares"] == 8
        assert len(data["ventes_immeuble"]) == 1
        assert data["rattachement"]["elargi_batiment"] == 0
        assert "private-rnb-detail" not in response.text + caplog.text

    async def test_inventory_failure_is_local_and_keeps_parcel_context(
        self,
        client,
        monkeypatch,
        caplog,
    ):
        _patch_fetches(
            monkeypatch,
            batiment_rows=[_dpe_row("private-neighbour", identifiant_ban=CLE_ANGLE)],
            rnb_cles=(CLE_ANGLE,),
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )
        monkeypatch.setattr(
            "app.logements.router.load_dpe_inventory_result",
            AsyncMock(side_effect=RuntimeError("private-inventory-detail")),
        )

        response = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert response.status_code == 200
        data = response.json()["donnees"]
        assert data["dpe_inventory"]["state"] == "unavailable"
        assert data["dpe_inventory"]["total"] is None
        assert data["dpe_attribution"] == {
            "version": 2,
            "status": "error",
            "reason": "temporary_error",
            "diagnostic": None,
            "candidates": [],
            "building_context": None,
            "graph_state": "not_checked",
        }
        assert data["detection"]["signaux"]["dpe_appart"]["presence"] == "error"
        assert data["denominateur"]["lots_declares"] == 8
        assert len(data["ventes_immeuble"]) == 1
        assert data["distribution_dpe"] == {}
        assert data["rattachement"] == {
            "exact": 0,
            "elargi_batiment": 0,
            "spatial": 0,
        }
        assert "private-neighbour" not in response.text
        assert "private-inventory-detail" not in response.text + caplog.text

        from app.logements import router as logements_router

        logements_router.cles_ban_batiment_rnb.assert_not_awaited()
        logements_router.dpe_par_identifiants_ban.assert_not_awaited()

    async def test_identity_unavailable_skips_impossible_rnb_context(
        self,
        client,
        monkeypatch,
    ):
        _patch_fetches(
            monkeypatch,
            batiment_rows=[_dpe_row("private-neighbour", identifiant_ban=CLE_ANGLE)],
            rnb_cles=(CLE_ANGLE,),
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )
        identity_unavailable = DpeInventoryServiceResult(
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
        monkeypatch.setattr(
            "app.logements.router.load_dpe_inventory_result",
            AsyncMock(return_value=identity_unavailable),
        )

        response = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert response.status_code == 200
        data = response.json()["donnees"]
        assert data["dpe_inventory"]["state"] == "identity_unavailable"
        assert data["dpe_attribution"]["status"] == "absent"
        assert data["dpe_attribution"]["reason"] == "identity_unavailable"
        assert data["distribution_dpe"] == {}
        assert data["rattachement"] == {
            "exact": 0,
            "elargi_batiment": 0,
            "spatial": 0,
        }
        assert data["denominateur"]["lots_declares"] == 8
        assert len(data["ventes_immeuble"]) == 1
        assert "private-neighbour" not in response.text

        from app.logements import router as logements_router

        logements_router.cles_ban_batiment_rnb.assert_not_awaited()
        logements_router.dpe_par_identifiants_ban.assert_not_awaited()


class TestNoPriceInPayload:
    """D-16 narrowed by D-17: no amount OUTSIDE ``ventes_immeuble``.

    The Phase 48 assertion (« no monetary word anywhere in the payload ») is
    revised here for the same reason the schema gate is: the sale rows now
    carry the price of a reconstructed MUTATION, always beside its class.
    What remains verifiable by grep is the half that still holds and that
    actually protected the user -- a logement card, a detection signal, a
    denominator never carry an amount.
    """

    async def test_no_price_outside_the_sales_rows(self, client, monkeypatch):
        _patch_fetches(
            monkeypatch,
            exact_rows=[_dpe_row("dpe-1", etage=2, complement="Bat A Porte 12")],
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        donnees = resp.json()["donnees"]
        donnees.pop("ventes_immeuble")
        reste = json.dumps(donnees).lower()
        for interdit in ("valeur_fonciere", "prix", "montant"):
            assert interdit not in reste, f"{interdit} leaked outside ventes_immeuble"

    async def test_every_amount_travels_with_its_label(self, client, monkeypatch):
        """D-17: the guarantee that replaced the interdiction, seen from outside."""
        _patch_fetches(
            monkeypatch,
            exact_rows=[_dpe_row("dpe-1", etage=2, complement="Bat A Porte 12")],
            copro_rows=[_copro_row()],
            dvf_rows=[_dvf_row()],
        )

        resp = await client.get(
            "/v1/bien/logements",
            params={"id_parcelle": ID_PARCELLE, "ban_id": CLE_RESOLUE},
        )

        assert resp.status_code == 200
        ventes = resp.json()["donnees"]["ventes_immeuble"]
        assert ventes, "the fixture must publish a sale row"
        for vente in ventes:
            assert vente["valeur_fonciere"] == 250000.0
            assert vente["classe_mutation"] == "residentiel_appartement"
            if vente["prix_m2"] is not None:
                assert vente["mono_local"] is True
