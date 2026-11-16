"""ARCEP import to the per-building shape, end to end (plan 04.1-16).

Two departemental files go through the real runner (``TinyArcep`` of
``test_flat_dropped_before_indexes``): the published table holds one row
per building and address, whose offers read back by ``unnest`` are exactly
the source rows (duplicates and empty speeds included); a second import that
brings a new (operator, technology, speed) triple keeps every existing code.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_arcep_par_immeuble_pipeline.py
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import psycopg
import pytest

from app.etl_arcep.config import OUTPUT_COLUMNS
from tests.integration import test_flat_dropped_before_indexes as flat
from tests.integration.test_flat_dropped_before_indexes import TinyArcep, run_arcep

pytestmark = pytest.mark.destructive
# The harness fixtures of the flat-table test: migrated base, empty tables.
migrated = flat.migrated
db = flat.db

# (id_imb, insee, voie, numero, technologie, debit_max, operateur)
PREMIER = (
    ("IMB-1", "44109", "RUE DE LA GARE", "1", "FTTH", "1000", "OP1"),
    ("IMB-1", "44109", "RUE DE LA GARE", "1", "FTTH", "1000", "OP1"),  # duplicate
    ("IMB-1", "44109", "RUE DE LA GARE", "1", "CU", "", "OP2"),  # empty speed
    ("IMB-2", "44109", "RUE DE LA GARE", "1", "FTTH", "1000", "OP1"),  # same address
    ("IMB-3", "85191", "PLACE DU PORT", "7", "FTTH", "100", "OP3"),
)
NOUVELLE_OFFRE = ("IMB-3", "85191", "PLACE DU PORT", "7", "5G", "300", "OP4")


def _fichiers(tmp_path: Path, lignes) -> list[Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    files = []
    for dept in ("44", "85"):
        path = tmp_path / f"arcep_{dept}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            for imb, insee, voie, numero, techno, debit, ope in lignes:
                if insee[:2] != dept:
                    continue
                writer.writerow(
                    {
                        "id_imb": imb,
                        "code_insee": insee,
                        "voie": voie,
                        "numero": numero,
                        "complement": "",
                        "technologie": techno,
                        "debit_max": debit,
                        "operateur": ope,
                        "code_departement": dept,
                    }
                )
        files.append(path)
    return files


def _offres_servies(dsn: str) -> Counter:
    with psycopg.connect(dsn) as conn:
        return Counter(
            conn.execute(
                "SELECT e.id_imb, e.code_insee, e.voie, e.numero, o.technologie, "
                "coalesce(o.debit_max, ''), o.operateur FROM eligibilite_fixe e "
                "CROSS JOIN LATERAL unnest(e.offres) AS u(code) "
                "JOIN eligibilite_offres o ON o.code = u.code"
            ).fetchall()
        )


def _dictionnaire(dsn: str) -> dict:
    with psycopg.connect(dsn) as conn:
        return {
            (ope, techno, debit): code
            for code, ope, techno, debit in conn.execute(
                "SELECT code, operateur, technologie, debit_max FROM eligibilite_offres"
            ).fetchall()
        }


def test_offers_read_back_are_the_source_rows_and_codes_are_stable(
    db, tmp_path, monkeypatch
):
    with psycopg.connect(db) as conn:
        conn.execute("DELETE FROM eligibilite_offres")
    premier = TinyArcep(db, _fichiers(tmp_path / "un", PREMIER))
    assert run_arcep(premier, tmp_path / "un", monkeypatch).success

    assert _offres_servies(db) == Counter(PREMIER)
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM eligibilite_fixe").fetchone() == (3,)
    codes = _dictionnaire(db)
    assert ("OP2", "CU", None) in codes  # an empty speed is NULL, never ''

    second = TinyArcep(db, _fichiers(tmp_path / "deux", (*PREMIER, NOUVELLE_OFFRE)))
    assert run_arcep(second, tmp_path / "deux", monkeypatch).success

    assert _offres_servies(db) == Counter((*PREMIER, NOUVELLE_OFFRE))
    apres = _dictionnaire(db)
    assert {k: apres[k] for k in codes} == codes  # never renumbered
    assert apres[("OP4", "5G", "300")] == max(codes.values()) + 1
