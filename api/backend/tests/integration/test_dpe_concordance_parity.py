"""Parity of the served concordance with the measurement script (04-19, N-12).

The same fixture rows go through the unchanged script
(``FAITS_DOCUMENTS_BASE_SQL`` of ``scripts/mesure_dpe_documents.py``) and
through the served module (``app.dpe.concordance``: SQL facts, Python
verdict): the verdict must be the same for every document.

Run only through ``scripts/run_contract_integration.ps1``. The module owns
temporary tables on the disposable database and never reads production data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import psycopg as pg_psycopg

from app.dpe.concordance import (
    FAITS_DOCUMENTS_SQL,
    FaitsConcordance,
    concordance_adresse,
)
from scripts import mesure_dpe_documents as mesure

pytestmark = pytest.mark.destructive

# The module query, with the bind style of psycopg (no SQL rewritten by hand).
MODULE_SQL = str(
    text(FAITS_DOCUMENTS_SQL).compile(
        dialect=pg_psycopg.dialect(), compile_kwargs={"render_postcompile": False}
    )
)


@pytest.fixture()
def conn(isolated_pg_dsn: str) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(
        isolated_pg_dsn, row_factory=dict_row, connect_timeout=5
    )
    # Same temporary tables as ``test_mesure_dpe_documents_sql.py``, with the
    # technical ``id`` the served query keys the page documents on.
    connection.execute(
        """
        CREATE TEMP TABLE diagnostics_dpe (
            id bigint, code_departement text NOT NULL, regime_dpe text,
            type_batiment text, is_tombstone boolean DEFAULT false,
            position geometry(Point, 4326), numero_dpe text NOT NULL,
            identifiant_ban text, date_etablissement_dpe date,
            date_fin_validite_dpe date,
            lifecycle_facts jsonb DEFAULT '{}'::jsonb, adresse_brut text,
            adresse_complete_brut text, complement_adresse_logement text,
            code_postal_brut text, nom_commune_brut text,
            id_rnb text, numero_dpe_remplace text, etiquette_dpe text,
            surface_habitable numeric, provenance_id_rnb text
        );
        CREATE TEMP TABLE adresses (
            id bigint, code_departement text, code_commune text, numero text,
            rep text, nom_voie text, code_postal text, ban_id text,
            position geometry(Point, 4326)
        );
        CREATE TEMP TABLE parcelles (
            id bigint, id_parcelle text, code_departement text,
            geometrie geometry(Polygon, 4326)
        );
        CREATE TEMP TABLE batiments (
            id bigint, code_departement text, usage_1 text,
            geometrie geometry(Polygon, 4326)
        );
        """
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


# (raw address, BAN number, BAN suffix, BAN street, expected verdict)
CAS = [
    ("12 rue du Port", "12", None, "Rue du Port", "concordant"),
    ("12 bis rue du Portail", "12", None, "Rue du Port", "ecart"),
    ("12 rue du Portail", "12", None, "Rue du Port", "ecart"),
    ("12 bis rue du Port", "12", None, "Rue du Port", "ecart"),
    ("12-14 rue du Port", "12", None, "Rue du Port", "non_evaluable"),
    ("12 quinquies rue du Port", "12", "quinquies", "Rue du Port", "concordant"),
    ("12B rue du Port", "12", "B", "Rue du Port", "concordant"),
    ("12 zz rue du Port", "12", None, "Rue du Port", "ecart"),  # unknown suffix
    ("12 rue du Port", "12", "b1", "Rue du Port", "non_evaluable"),  # BAN suffix
    ("12 rue du Port", "12 bis", None, "Rue du Port", "non_evaluable"),
    ("12", "12", None, "Rue du Port", "non_evaluable"),  # street absent
    (None, "12", None, "Rue du Port", "non_evaluable"),
    ("12 rue du Port", "12", None, None, "non_evaluable"),  # BAN street absent
    ("0012 Rue de l'Église", "12", None, "Rue de l'Eglise", "concordant"),
    ("14 rue du Port", "12", None, "Rue du Port", "ecart"),
    ("rue du Port", "12", None, "Rue du Port", "non_evaluable"),
]


@pytest.mark.parametrize(("brut", "numero", "rep", "voie", "attendu"), CAS)
def test_the_module_gives_the_script_verdict_on_every_row(
    conn: psycopg.Connection, brut, numero, rep, voie, attendu
) -> None:
    conn.execute(
        """INSERT INTO adresses (id, code_departement, code_commune, numero,
        rep, nom_voie, code_postal, ban_id, position)
        VALUES (1, '44', '44109', %s, %s, %s, '44000', '44109_a001_00012',
                ST_SetSRID(ST_Point(-1.55, 47.21), 4326))""",
        (numero, rep, voie),
    )
    conn.execute(
        """INSERT INTO diagnostics_dpe (id, code_departement, regime_dpe,
        type_batiment, position, numero_dpe, identifiant_ban,
        date_etablissement_dpe, adresse_brut)
        VALUES (7, '44', 'existant', 'maison',
                ST_SetSRID(ST_Point(-1.55, 47.21), 4326), 'DOC-A',
                '44109_a001_00012', %s, %s)""",
        (date(2024, 1, 1), brut),
    )

    script = conn.execute(
        mesure.FAITS_DOCUMENTS_BASE_SQL,
        {
            "departements": ["44"],
            "date_import": date(2026, 1, 1),
            "date_mesure": date(2026, 1, 2),
        },
    ).fetchall()
    module = conn.execute(MODULE_SQL, {"ids": [7], "department": "44"}).fetchall()

    assert len(script) == 1 and len(module) == 1
    assert module[0]["cle_jointe"] is True
    verdict_module = concordance_adresse(FaitsConcordance.depuis_ligne(module[0]))
    assert verdict_module == script[0]["concordance_adresse"] == attendu
