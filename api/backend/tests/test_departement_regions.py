"""Region of each departement, official and accented (plan 04.7-10, decision 3).

``/v1/departements`` served « Ile-de-France » and « La Reunion »: the page
title, the region links and the JSON-LD read this name, so it is the official
one (INSEE, COG), accents included. A change of values, not of shape.
"""

import pytest

from app.scope.model import DEPARTEMENTS_HEBERGEABLES
from app.zone.router import DEPT_REGION

pytestmark = pytest.mark.unit

# The 18 regions of the COG, written by hand from the INSEE list.
OFFICIAL_REGIONS = {
    "Auvergne-Rhône-Alpes",
    "Bourgogne-Franche-Comté",
    "Bretagne",
    "Centre-Val de Loire",
    "Corse",
    "Grand Est",
    "Hauts-de-France",
    "Île-de-France",
    "Normandie",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Pays de la Loire",
    "Provence-Alpes-Côte d'Azur",
    "Guadeloupe",
    "Martinique",
    "Guyane",
    "La Réunion",
    "Mayotte",
}


def test_every_hostable_departement_has_an_official_region():
    assert set(DEPT_REGION) == set(DEPARTEMENTS_HEBERGEABLES)
    assert set(DEPT_REGION.values()) == OFFICIAL_REGIONS


@pytest.mark.parametrize(
    ("code", "region"),
    [
        ("75", "Île-de-France"),
        ("974", "La Réunion"),
        ("13", "Provence-Alpes-Côte d'Azur"),
        ("69", "Auvergne-Rhône-Alpes"),
        ("25", "Bourgogne-Franche-Comté"),
        ("44", "Pays de la Loire"),
        ("2A", "Corse"),
    ],
)
def test_a_region_keeps_its_accents(code, region):
    assert DEPT_REGION[code] == region
