"""Tests: the DECLARED dwelling type of /v1/resolve (Phase 57 plan 18, D-41).

Before this plan, ``/v1/resolve`` carried no dwelling type at all, so
``decide_dvf_attribution`` answered ``type_bien_inconnu`` for EVERY parcel
holding mutations: the state ``compatible_parcelle`` was unreachable in
production. The exit is not to guess better, it is to ASK -- and to say that
one asked.

Three levels live here, and the negative ones are the point (D-40):

  1. Parsing: ``_parse_type_bien`` normalizes (strip/lower), reads an empty or
     absent value as the UNKNOWN, and REJECTS anything else with a 422. A typo
     that reads like a legitimate choice is worse than an error, because it
     would be believed.
  2. Provenance: a declaration is traced on every state produced
     (``type_bien_fourni_utilisateur`` + ``type_bien_source``), and the two
     halves are unconstructible apart. A provenance field that can lie is not
     a provenance.
  3. Wiring: the declared value travels router -> multi -> decision without
     being interpreted anywhere on the way, and NEVER promotes anything to
     ``exacte`` -- D-37 stays whole (a declaration is not a proof of the
     identity of the sold dwelling).

Same doctrine as ``test_resolve_dvf_parcelle.py`` for the HTTP level (AsyncMock
+ dependency_overrides, no real DB, ``type=gps`` to bypass the geocoder), and
same synthetic-only corpus as ``test_dvf_attribution.py`` (D-31).
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.auth.dependencies import check_quota, check_quota_hybrid
from app.auth.models import ApiKeyInfo
from app.common.exceptions import ErreurApiError
from app.dvf.attribution import TYPES_BIEN_ADMIS, decide_dvf_attribution
from app.dvf.schemas import (
    DVF_EVIDENCES,
    DVF_TYPES_BIEN_CONSULTES,
    EVIDENCE_TYPE_BIEN_UTILISATEUR,
    DvfAttribution,
)
from app.main import app


@pytest.fixture(autouse=True)
def _published_source_double(monkeypatch):
    """Model admitted DVF stock without replacing attribution or query failures."""
    monkeypatch.setattr(
        "app.resolve.multi.guard_publication", AsyncMock(return_value=None)
    )


from app.resolve.router import _parse_type_bien
from app.scope.dependencies import get_instance_scope
from tests.scope_doubles import FULL_SCOPE

# `asyncio_mode = "auto"` (pyproject) : les coroutines de ce module sont
# collectées sans marqueur, et les tests SYNCHRONES de parsing/provenance
# ci-dessous restent des tests synchrones — d'où l'absence de `pytestmark`.


# --- Synthetic corpus (D-31): configurations, never real addresses ----------

_LAT = 46.000001
_LON = -1.000001  # synthetic point (dept 85 double), not a real address
_ID_PARCELLE = "85047000AD0123"


def mutation(**overrides) -> dict:
    """One reconstructed mutation row -- appartement mono-local sale by default."""
    ligne: dict = {
        "id_mutation": "CASE-TB-0001",
        "date_mutation": date(2024, 6, 1),
        "nature_mutation": "Vente",
        "valeur_fonciere": 250000.0,
        "type_local": "Appartement",
        "surface_reelle_bati": 62,
        "nombre_pieces_principales": 3,
        "surface_terrain": None,
        "prix_m2": 4032.0,
        "adresse_numero": "1",
        "adresse_nom_voie": "rue synthetique",
        "code_postal": "85300",
        "nom_commune": "Commune-Test",
        "id_parcelle": _ID_PARCELLE,
        "code_type_local": "2",
        "longitude": _LON,
        "latitude": _LAT,
        "classe_mutation": "residentiel_appartement",
        "prix_m2_raison": None,
        "mutation_mono_local": True,
        "nb_locaux_maison": 0,
        "nb_locaux_appartement": 1,
        "nb_locaux_commercial": 0,
        "nb_lots_source": 1,
        "nb_parcelles": 1,
        "hors_stats_vefa": False,
        "surface_bati_totale": 62,
    }
    ligne.update(overrides)
    return ligne


_MAISON = {
    "id_mutation": "CASE-TB-MAISON",
    "type_local": "Maison",
    "code_type_local": "1",
    "classe_mutation": "residentiel_maison",
    "nb_locaux_maison": 1,
    "nb_locaux_appartement": 0,
    "surface_reelle_bati": 110,
    "surface_bati_totale": 110,
}


# --- Level 1: parsing, and what it REFUSES (D-40) ---------------------------


def test_le_router_consomme_le_vocabulaire_de_la_decision():
    """T-57-74: ONE closed vocabulary, exported by the decision module.

    A second list living in the router would drift apart by one release --
    exactly the defect class this phase closes.
    """
    assert TYPES_BIEN_ADMIS == ("maison", "appartement")


@pytest.mark.parametrize(
    "brut,attendu",
    [
        ("maison", "maison"),
        ("appartement", "appartement"),
        ("Appartement", "appartement"),
        ("  MAISON  ", "maison"),
    ],
)
def test_une_valeur_du_vocabulaire_est_normalisee_et_acceptee(brut: str, attendu: str):
    """Casing and surrounding blanks are typography, not a different choice."""
    assert _parse_type_bien(brut) == attendu


@pytest.mark.parametrize("brut", [None, "", "   "])
def test_le_parametre_absent_ou_vide_vaut_l_inconnu(brut):
    """Absent or empty is the UNKNOWN -- the behaviour of every existing caller."""
    assert _parse_type_bien(brut) is None


@pytest.mark.parametrize(
    "brut", ["loft", "1", "maison,appartement", "appartment", "terrain", "MAISON2"]
)
def test_une_valeur_hors_vocabulaire_est_rejetee_en_422(brut: str):
    """D-40: the gate proves it BLOCKS -- no silent degradation to the unknown.

    Ramener une faute de frappe à l'inconnu produirait un écran plausible sur
    une valeur inventée : l'erreur explicite est la seule sortie honnête.
    """
    with pytest.raises(ErreurApiError) as exc:
        _parse_type_bien(brut)

    assert exc.value.code == 422
    assert "maison" in str(exc.value.detail) and "appartement" in str(exc.value.detail)


# --- Level 2: the provenance of the decision (D-41) -------------------------


def test_le_vocabulaire_d_evidence_porte_la_declaration():
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in DVF_EVIDENCES


def test_source_utilisateur_sans_evidence_est_inconstructible():
    """A provenance field that can lie is not a provenance."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            type_bien_source="utilisateur",
            type_bien_consulte="appartement",
        )


def test_source_utilisateur_sans_valeur_consultee_est_inconstructible():
    """CASE-TB-PROV-01 (D-40/D-41) : « déclaré » sans dire QUOI ne dit rien.

    La trace et la source étaient déjà solidaires ; la VALEUR l'est désormais
    aussi. Sans elle, un lecteur du payload voit qu'un type a servi à la
    décision sans pouvoir savoir lequel."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            type_bien_source="utilisateur",
            evidence=[EVIDENCE_TYPE_BIEN_UTILISATEUR],
        )


def test_valeur_consultee_sur_un_type_inconnu_est_inconstructible():
    """CASE-TB-PROV-02 (D-40/D-41) : réciproque. Une décision qui se dit prise
    sur un type inconnu n'en a consulté aucun."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            type_bien_consulte="maison",
        )


def test_valeur_consultee_hors_vocabulaire_est_inconstructible():
    """CASE-TB-PROV-03 (D-40) : le contrat refuse ce que le parseur refuse déjà.

    Le 422 du router protège l'entrée HTTP ; ce test protège le contrat
    lui-même, atteignable par tout autre appelant Python."""
    assert "loft" not in DVF_TYPES_BIEN_CONSULTES
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            type_bien_source="utilisateur",
            type_bien_consulte="loft",
            evidence=[EVIDENCE_TYPE_BIEN_UTILISATEUR],
        )


def test_evidence_de_declaration_sans_source_est_inconstructible():
    """Réciproque : la trace et la provenance voyagent ensemble, ou pas du tout."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            evidence=[EVIDENCE_TYPE_BIEN_UTILISATEUR],
        )


def test_source_hors_vocabulaire_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            type_bien_source="devine",
            type_bien_consulte="appartement",
        )


def test_une_absence_ne_porte_toujours_aucune_preuve_verifiee():
    """La détente du validateur est BORNÉE au marqueur de déclaration."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            evidence=[EVIDENCE_TYPE_BIEN_UTILISATEUR, "mono_local"],
            type_bien_source="utilisateur",
            type_bien_consulte="appartement",
        )


@pytest.mark.parametrize(
    "mutations,statut_attendu",
    [
        ([], "absente"),
        ([mutation(**_MAISON)], "non_attribuable"),
        ([mutation()], "compatible_parcelle"),
        ("entree-illisible", "error"),
    ],
)
def test_un_type_declare_est_trace_sur_tous_les_etats(mutations, statut_attendu: str):
    """« Le type a été déclaré ET rien n'est compatible » est un fait distinct
    de « le type est inconnu » — y compris sur les absences et sur la panne."""
    decision = decide_dvf_attribution(mutations, type_bien="appartement")

    assert decision.status == statut_attendu
    assert decision.type_bien_source == "utilisateur"
    assert decision.type_bien_consulte == "appartement"
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in decision.evidence


@pytest.mark.parametrize("mutations", [[], [mutation()], "entree-illisible"])
def test_sans_type_declare_aucune_trace_de_declaration(mutations):
    decision = decide_dvf_attribution(mutations, type_bien=None)

    assert decision.type_bien_source == "inconnu"
    assert decision.type_bien_consulte is None
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR not in decision.evidence


# --- Level 2 bis: what a declaration does, and what it never does -----------


def test_le_type_declare_rend_compatible_parcelle_et_jamais_exacte():
    """Le cas le PLUS FAVORABLE : appartement mono-local, vente, surface
    présente, sur la parcelle exacte. Il s'arrête à la compatibilité
    parcellaire — une déclaration n'est pas une preuve d'identité du logement
    vendu (D-37/D-41)."""
    decision = decide_dvf_attribution([mutation()], type_bien="appartement")

    assert decision.status == "compatible_parcelle"
    assert decision.status != "exacte", "une déclaration ne promeut jamais en exacte"
    assert decision.preuve_locale is None
    assert decision.mutation is not None
    assert decision.mutation.id_mutation == "CASE-TB-0001"


def test_l_absence_du_parametre_laisse_le_comportement_actuel_inchange():
    """Le comportement d'avant ce plan, verrouillé : sans type déclaré, toute
    parcelle portant des mutations rend `type_bien_inconnu`."""
    decision = decide_dvf_attribution([mutation()], type_bien=None)

    assert decision.status == "non_attribuable"
    assert decision.reason == "type_bien_inconnu"
    assert decision.mutation is None
    assert decision.type_bien_source == "inconnu"


def test_un_type_declare_incompatible_donne_une_absence_expliquee():
    """`maison` déclaré sur une parcelle dont la seule vente est un appartement."""
    decision = decide_dvf_attribution([mutation()], type_bien="maison")

    assert decision.status == "non_attribuable"
    assert decision.reason == "type_incompatible"
    assert decision.type_bien_source == "utilisateur"


# --- Level 3: HTTP wiring ---------------------------------------------------

_PARCELLE = {
    "id_parcelle": _ID_PARCELLE,
    "commune": "85047",
    "prefixe": "000",
    "section": "AD",
    "numero": "0123",
    "contenance": 500,
    "centroide_lat": _LAT,
    "centroide_lon": _LON,
    "geometrie_geojson": "{}",
}

_BAN_ROW = {
    "code_commune": "85047",
    "ban_id": "85047_synthetic",
    "nom_voie": "rue synthetique",
    "numero": "1",
    "latitude": _LAT,
    "longitude": _LON,
    "nom_commune": "Commune-Test",
    "code_postal": "85300",
    "distance_m": 5.0,
    "rep": None,
}


def _auth_client() -> AsyncClient:
    fake = ApiKeyInfo(
        id=1,
        name="test-key",
        quota_monthly=999999,
        revoked=False,
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[check_quota] = lambda: fake
    app.dependency_overrides[check_quota_hybrid] = lambda: fake
    app.dependency_overrides[get_instance_scope] = lambda: FULL_SCOPE
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _resolve(query: str, rows: list[dict], decide=None):
    """Run a GPS-mode resolve whose « dvf » dataset returns *rows*."""
    async with _auth_client() as client:
        with (
            patch(
                "app.resolve.router.reverse_geocoder",
                AsyncMock(return_value=[_BAN_ROW]),
            ),
            patch(
                "app.resolve.router.parcelle_par_point",
                AsyncMock(return_value=[_PARCELLE]),
            ),
            patch(
                "app.resolve.multi.dvf_par_id_parcelle",
                AsyncMock(return_value=rows),
            ),
        ):
            if decide is None:
                return await client.get(query)
            with patch("app.resolve.multi.decide_dvf_attribution", decide):
                return await client.get(query)


_URL = f"/v1/resolve?type=gps&q={_LAT},{_LON}&datasets=dvf"


async def test_le_type_declare_atteint_la_decision_sans_etre_interprete():
    """CASE-TB-WIRE-01: the parameter travels router -> multi -> decision, and
    the decision is still computed exactly ONCE per resolution."""
    decide = MagicMock(
        return_value=DvfAttribution(status="absente", reason="aucune_mutation")
    )
    resp = await _resolve(f"{_URL}&type_bien=appartement", [mutation()], decide=decide)

    assert resp.status_code == 200, resp.text
    assert decide.call_count == 1
    assert decide.call_args.kwargs["type_bien"] == "appartement"

    app.dependency_overrides.clear()


async def test_la_casse_du_parametre_est_normalisee_avant_la_decision():
    """CASE-TB-WIRE-02: la normalisation a lieu UNE fois, dans le router."""
    decide = MagicMock(
        return_value=DvfAttribution(status="absente", reason="aucune_mutation")
    )
    resp = await _resolve(f"{_URL}&type_bien=Appartement", [mutation()], decide=decide)

    assert resp.status_code == 200, resp.text
    assert decide.call_args.kwargs["type_bien"] == "appartement"

    app.dependency_overrides.clear()


async def test_sans_le_parametre_la_decision_recoit_l_inconnu_explicite():
    """CASE-TB-WIRE-03: le contrat de la Phase 57 plan 12, intact."""
    decide = MagicMock(
        return_value=DvfAttribution(status="absente", reason="aucune_mutation")
    )
    resp = await _resolve(_URL, [mutation()], decide=decide)

    assert resp.status_code == 200, resp.text
    assert decide.call_args.kwargs["type_bien"] is None

    app.dependency_overrides.clear()


async def test_la_reponse_publie_compatible_parcelle_avec_sa_provenance():
    """CASE-TB-WIRE-04: l'état que ce plan rend ATTEIGNABLE, vu du payload —
    et sa provenance lisible sans ouvrir le code."""
    resp = await _resolve(f"{_URL}&type_bien=appartement", [mutation()])

    assert resp.status_code == 200, resp.text
    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "compatible_parcelle"
    assert attribution["preuve_locale"] is None
    assert attribution["type_bien_source"] == "utilisateur"
    assert attribution["type_bien_consulte"] == "appartement"
    assert EVIDENCE_TYPE_BIEN_UTILISATEUR in attribution["evidence"]
    assert attribution["mutation"]["id_mutation"] == "CASE-TB-0001"

    app.dependency_overrides.clear()


async def test_sans_parametre_la_reponse_reste_type_bien_inconnu():
    """CASE-TB-WIRE-05: absence du paramètre = comportement actuel, strictement."""
    resp = await _resolve(_URL, [mutation()])

    assert resp.status_code == 200, resp.text
    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["status"] == "non_attribuable"
    assert attribution["reason"] == "type_bien_inconnu"
    assert attribution["type_bien_source"] == "inconnu"
    assert attribution["type_bien_consulte"] is None
    assert attribution["mutation"] is None

    app.dependency_overrides.clear()


async def test_une_valeur_inventee_rend_422_et_aucune_decision():
    """CASE-TB-WIRE-06 (D-40): la requête est refusée AVANT toute décision."""
    decide = MagicMock(
        return_value=DvfAttribution(status="absente", reason="aucune_mutation")
    )
    resp = await _resolve(f"{_URL}&type_bien=loft", [mutation()], decide=decide)

    assert resp.status_code == 422, resp.text
    assert decide.call_count == 0, (
        "aucune décision n'est produite sur une valeur inventée"
    )
    assert "donnees" not in resp.json() or not resp.json().get("donnees")

    app.dependency_overrides.clear()


async def test_l_historique_de_parcelle_ne_depend_pas_du_type_declare():
    """CASE-TB-WIRE-07 (D-11/D-17): le type qualifie la DÉCISION, il ne filtre
    jamais l'historique publié par `datasets.dvf`."""
    lignes = [mutation(), mutation(**_MAISON)]

    sans = await _resolve(_URL, lignes)
    app.dependency_overrides.clear()
    avec = await _resolve(f"{_URL}&type_bien=maison", lignes)

    lignes_sans = sans.json()["donnees"][0]["datasets"]["dvf"]
    lignes_avec = avec.json()["donnees"][0]["datasets"]["dvf"]
    ids_sans = [row["id_mutation"] for row in lignes_sans]
    ids_avec = [row["id_mutation"] for row in lignes_avec]
    assert ids_sans == ids_avec == ["CASE-TB-0001", "CASE-TB-MAISON"]

    app.dependency_overrides.clear()


async def test_aucune_inference_du_type_depuis_dvf_ni_depuis_le_dpe():
    """CASE-TB-WIRE-08 (D-41): une parcelle dont TOUTES les ventes sont des
    appartements ne fait pas de la fiche un appartement. Sans déclaration, la
    décision reste l'inconnu — inférer serait circulaire."""
    resp = await _resolve(_URL, [mutation(), mutation(id_mutation="CASE-TB-0002")])

    attribution = resp.json()["donnees"][0]["dvf_attribution"]
    assert attribution["reason"] == "type_bien_inconnu"

    app.dependency_overrides.clear()
