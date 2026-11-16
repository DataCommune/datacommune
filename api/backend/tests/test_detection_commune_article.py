"""R-15: an article ending the input names no commune (2026-11-10).

While « 12 rue de la paix » is being typed, « 12 rue de la » ends on « la »:
``_DETECT_CITY`` matched it with ``LIKE 'la %' … LIMIT 1``, without order,
and the options jumped to a commune « La … » taken at random (La
Bachellerie, Dordogne, on datacommune.fr). A linking word alone at the end
of the input belongs to the street being typed. A commune name that only
starts with an article (« La Roche-sur-Yon ») is still detected.

Only the session is replaced: the city lookup answers a row for any
candidate that is, or starts, a commune name of the table below, as the
``LIKE`` of ``communes_lookup`` would.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ban.queries import _detect_city

pytestmark = pytest.mark.unit

COMMUNES = {"la bachellerie": "24021", "la roche-sur-yon": "85191"}


class _Lookup:
    def __init__(self) -> None:
        self.candidats: list[str] = []

    def begin_nested(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement, params=None):
        result = MagicMock()
        result.all.return_value = []
        candidat = (params or {}).get("candidate")
        ligne = None
        if candidat is not None:
            self.candidats.append(candidat)
            for nom, code in COMMUNES.items():
                if nom == candidat or nom.startswith(candidat + " "):
                    ligne = SimpleNamespace(nom_commune=nom, code_commune=code)
                    break
        result.first.return_value = ligne
        return result


@pytest.mark.parametrize(
    "saisie", ["12 rue de la", "rue de la", "3 allee des", "8 rue du"]
)
async def test_an_article_ending_the_input_names_no_commune(saisie):
    session = _Lookup()

    assert await _detect_city(session, saisie) is None


async def test_a_commune_starting_with_an_article_is_still_detected():
    session = _Lookup()

    trouvee = await _detect_city(session, "12 rue des lilas la roche-sur-yon")

    assert trouvee is not None
    assert trouvee[2] == ["85191"]
