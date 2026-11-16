"""LB-04 / A6-04: proxy schemas must not republish unknown upstream fields.

Before this fix, every proxy response model declared ``extra="allow"``, so any
field added upstream (RNB, Sirene, Georisques, API Carto, BDNB) was silently
re-served to DataCommune clients without filtering.

Arbitrage (documented in the fix commit): ``extra="ignore"`` — not
``"forbid"``. A proxy is fail-closed on availability but must NOT break on a
NEW field added upstream: ``forbid`` would turn any upstream schema addition
into a 502 for every request (fail-closed on data, wrong direction).
``ignore`` drops unknown fields from the published contract while keeping the
proxy resilient.
"""

import logging

import pytest
from pydantic import BaseModel

from app.bdnb.schemas import BatimentBDNB
from app.errial.schemas import PPR as PPR_ERRIAL
from app.georisques.schemas import (
    ExpositionArgiles,
    InstallationClassee,
    PotentielRadon,
    RisqueNaturel,
    SitePollue,
)
from app.rnb.schemas import AdresseRNB, BatimentRNB, ExternalId
from app.sirene.schemas import ResultatEntreprise
from app.urbanisme.schemas import Prescription, ZonePlu

pytestmark = pytest.mark.unit

# Every model that used to carry extra="allow".
MODELES_PROXY = [
    ResultatEntreprise,
    AdresseRNB,
    ExternalId,
    BatimentRNB,
    RisqueNaturel,
    ExpositionArgiles,
    PotentielRadon,
    InstallationClassee,
    SitePollue,
    ZonePlu,
    Prescription,
    BatimentBDNB,
    PPR_ERRIAL,
]


@pytest.mark.parametrize("modele", MODELES_PROXY, ids=lambda m: m.__name__)
def test_champ_upstream_inconnu_non_republie(modele):
    """An unknown upstream field is dropped: absent from dump AND JSON."""
    instance = modele.model_validate({"champ_inconnu_upstream": "valeur"})
    assert "champ_inconnu_upstream" not in instance.model_dump()
    assert "champ_inconnu_upstream" not in instance.model_dump_json()
    assert not hasattr(instance, "champ_inconnu_upstream")


@pytest.mark.parametrize("modele", MODELES_PROXY)
def test_modele_sans_extra_allow(modele):
    """No proxy model may re-declare extra='allow'."""
    assert modele.model_config.get("extra") != "allow"


@pytest.mark.parametrize("modele", MODELES_PROXY, ids=lambda m: m.__name__)
def test_champ_inconnu_warning_structure(modele, caplog):
    """An unknown upstream field must emit a structured WARNING log.

    The drop is silent today: operators have no signal that the upstream
    contract drifted. The warning must carry the model name and the unknown
    field names as structured attributes.
    """
    with caplog.at_level(logging.WARNING):
        modele.model_validate({"champ_inconnu_upstream": "valeur"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, f"aucun warning loggé pour {modele.__name__}"
    rec = warnings[0]
    assert "champ_inconnu_upstream" in getattr(rec, "unknown_fields", [])
    assert getattr(rec, "schema_name", "") == modele.__name__


def test_champs_connus_conserves():
    """The allowlist keeps working: known fields survive validation."""
    batiment = BatimentRNB.model_validate(
        {
            "rnb_id": "ABCD1234",
            "status": "constructed",
            "is_active": True,
            "champ_inconnu_upstream": {"nested": True},
            "addresses": [
                {
                    "street": "Rue de la Paix",
                    "champ_inconnu_adresse": 1,
                }
            ],
        }
    )
    assert batiment.rnb_id == "ABCD1234"
    assert batiment.status == "constructed"
    assert batiment.addresses[0].street == "Rue de la Paix"
    dumped = batiment.model_dump()
    assert "champ_inconnu_upstream" not in dumped
    assert "champ_inconnu_adresse" not in dumped["addresses"][0]


def test_tous_les_modeles_proxy_couverts():
    """Guard against future proxy models reintroducing extra='allow'.

    Re-imports every schema module of the five proxy packages and fails if
    any BaseModel subclass declares extra='allow'.
    """
    import importlib

    fautifs: list[str] = []
    for nom_module in (
        "app.sirene.schemas",
        "app.rnb.schemas",
        "app.georisques.schemas",
        "app.urbanisme.schemas",
        "app.bdnb.schemas",
        "app.errial.schemas",
    ):
        module = importlib.import_module(nom_module)
        for nom in dir(module):
            obj = getattr(module, nom)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.model_config.get("extra") == "allow"
            ):
                fautifs.append(f"{nom_module}.{nom}")
    assert fautifs == [], f"extra='allow' réintroduit dans : {fautifs}"
