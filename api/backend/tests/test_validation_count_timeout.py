"""The exact ``count(*)`` of ``run_all_validators`` runs under the scan delay.

A fixed 8 s would not count the national ``parcelles`` (94.8 M rows),
``mutations``, ``diagnostics_dpe`` nor ``adresses``: the count takes the
delay of the validation scans (``etl_validation_scan_timeout_s``), unless a
validator sets its own.
"""

import pytest

from app.config import settings
from app.validation import base
from app.validation.base import BaseValidator

pytestmark = pytest.mark.unit


class _Validateur(BaseValidator):
    dataset_name = "parcelles"
    production_table_name = "parcelles"

    def get_source_row_count(self) -> int | None:
        return None


class _Connexion:
    def __init__(self, journal, admis=True):
        self.journal = journal
        self.admis = admis

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_read_only(self, valeur):
        self.journal.append(("read_only", valeur))

    def execute(self, requete, parametres=None):
        self.journal.append(("execute", str(requete), parametres))
        valeur = self.admis if "advisory" in str(requete) else 94_800_000

        class Resultat:
            def fetchone(self):
                return (valeur,)

        return Resultat()


ADMIS = {"valeur": True}


@pytest.fixture
def journal(monkeypatch):
    lignes: list = []
    ADMIS["valeur"] = True
    monkeypatch.setattr(
        base.psycopg,
        "connect",
        lambda dsn, connect_timeout: _Connexion(lignes, ADMIS["valeur"]),
    )
    return lignes


def _delai(journal) -> str:
    (parametres,) = [p for x in journal if x[0] == "execute" and "set_config" in x[1]
                     for p in [x[2]]]  # fmt: skip
    return parametres[0]


def test_the_delay_is_local_to_the_transaction_and_set_before_the_count(journal):
    _Validateur().get_local_row_count("dsn")
    requetes = [x[1] if x[0] == "execute" else x[0] for x in journal]
    assert requetes[0] == "read_only"
    assert requetes[1] == "SELECT set_config('statement_timeout', %s, true)"
    assert "pg_try_advisory_xact_lock_shared" in requetes[2]
    assert "count(*)" in requetes[3]


def test_the_count_is_refused_while_a_publication_runs(journal):
    from app.commune.dependencies import PUBLICATION_LOCK_ID
    from app.etl.validation import ValidationUnavailableError

    ADMIS["valeur"] = False
    with pytest.raises(ValidationUnavailableError):
        _Validateur().get_local_row_count("dsn")
    assert journal[-1][2] == (PUBLICATION_LOCK_ID,)
    assert not any("count(*)" in str(x) for x in journal)


def test_the_count_takes_the_delay_of_the_validation_scans(journal):
    assert _Validateur().get_local_row_count("dsn") == 94_800_000
    assert _delai(journal) == str(settings.etl_validation_scan_timeout_s * 1000)
    assert ("read_only", True) in journal


def test_the_delay_follows_its_setting(journal, monkeypatch):
    monkeypatch.setattr(settings, "etl_validation_scan_timeout_s", 1800)
    _Validateur().get_local_row_count("dsn")
    assert _delai(journal) == "1800000"


def test_a_validator_may_set_its_own_delay(journal):
    validateur = _Validateur()
    validateur.statement_timeout_ms = 50
    validateur.get_local_row_count("dsn")
    assert _delai(journal) == "50"
