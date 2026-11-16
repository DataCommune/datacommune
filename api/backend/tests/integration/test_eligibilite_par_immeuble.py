"""``eligibilite_fixe`` per building, served as before (plan 04.1-16).

A synthetic base at revision 105 holds the source shape (one row per offer)
with the five shape cases of the plan: several offers of a building, an
exact duplicate offer, a NULL speed, two ``id_imb`` at one address, and an
``id_imb`` at two addresses (absent from the measured 44/85 base, kept
possible). The answers of the routes are read with the SQL of revision 105
(the oracle, copied below), the migration 106 runs, and the application
queries must give the same answers on the new shape: same multisets of rows,
same (technology, speed) sequence, same ``fibre_pct``. Downgrade then gives
back the same offer rows (but the technical ``id``), and the latest
publication of the dataset stays anchored on the relation it serves.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_eligibilite_par_immeuble.py
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg.rows import dict_row
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.connectivite.queries import eligibilite_adresse_exacte, eligibilite_par_adresse
from scripts.eligibilite_equivalence import comparer, exporter

pytestmark = pytest.mark.destructive

BACKEND = Path(__file__).resolve().parents[2]
AVANT = "105_dvf_adresse_suffixe"
APRES = "106_eligibilite_fixe_par_immeuble"
COMMUNE = "44109"
AUTRE = "44020"
# (id_imb, code_insee, voie, numero, complement, technologie, debit, operateur)
LIGNES = (
    # IMB-A: several offers, one duplicated exactly, one with a NULL speed.
    ("IMB-A", COMMUNE, "RUE DES LILAS", "12", None, "FO", "1000", "OPE1"),
    ("IMB-A", COMMUNE, "RUE DES LILAS", "12", None, "FO", "1000", "OPE1"),
    ("IMB-A", COMMUNE, "RUE DES LILAS", "12", None, "FO", "1000", "OPE2"),
    ("IMB-A", COMMUNE, "RUE DES LILAS", "12", None, "CU", "8", "OPE1"),
    ("IMB-A", COMMUNE, "RUE DES LILAS", "12", None, "HD", None, None),
    # Two buildings at one address (12 bis): never merged.
    ("IMB-B1", COMMUNE, "RUE DES LILAS", "12", "BIS", "FO", "1000", "OPE1"),
    ("IMB-B2", COMMUNE, "RUE DES LILAS", "12", "BIS", "CU", "8", "OPE1"),
    # One building at two addresses.
    ("IMB-C", COMMUNE, "RUE DU PORT", "4", None, "FO", "100", "OPE3"),
    ("IMB-C", COMMUNE, "RUE DU PORT PROLONGEE", "4", None, "FO", "100", "OPE3"),
    # Another commune without fibre.
    ("IMB-D", AUTRE, "PLACE DU MARCHE", "1", None, "CU", "8", "OPE1"),
)
# Oracle: the SQL of revision 105 (app.connectivite.queries before 04.1-16).
_ANCIENNE_PAR_ADRESSE = """
    SELECT id_imb, code_insee, voie, numero, technologie, debit_max, operateur
    FROM eligibilite_fixe
    WHERE {where}
    ORDER BY technologie, debit_max DESC NULLS LAST
    LIMIT 50
"""
_ANCIENS_CANDIDATS = """
    SELECT id_imb, voie, numero, complement, technologie, debit_max
    FROM eligibilite_fixe
    WHERE code_insee = %(code_insee)s AND numero = %(numero)s
      AND unaccent(lower(voie)) LIKE '%%' || unaccent(lower(%(nom_voie)s)) || '%%'
    ORDER BY id_imb, technologie, debit_max
"""
# (code_insee, nom_voie, numero) of eligibilite_par_adresse
APPELS_PAR_ADRESSE = (
    (COMMUNE, None, None),
    (COMMUNE, "lilas", None),
    (COMMUNE, "lilas", "12"),
    (COMMUNE, "port", "4"),
    (AUTRE, None, None),
)
# (code_insee, numero, rep, nom_voie) of eligibilite_adresse_exacte
APPELS_EXACTS = (
    (COMMUNE, "12", None, "RUE DES LILAS"),
    (COMMUNE, "12", "bis", "RUE DES LILAS"),
    (COMMUNE, "4", None, "RUE DU PORT"),
    (COMMUNE, "99", None, "RUE DES LILAS"),
)


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


def _alembic(dsn: str, action: str, revision: str) -> None:
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", dsn)
        context.setattr(settings, "database_url", dsn)
        getattr(command, action)(_config(dsn), revision)


def _ancien_par_adresse(conn, code_insee, nom_voie, numero) -> list[tuple]:
    clauses, params = ["code_insee = %(code_insee)s"], {"code_insee": code_insee}
    if nom_voie is not None:
        clauses.append(
            "unaccent(lower(voie)) LIKE '%%' || unaccent(lower(%(nom_voie)s)) || '%%'"
        )
        params["nom_voie"] = nom_voie
    if numero is not None:
        clauses.append("numero = %(numero)s")
        params["numero"] = numero
    sql = _ANCIENNE_PAR_ADRESSE.format(where=" AND ".join(clauses))
    return [tuple(row) for row in conn.execute(sql, params).fetchall()]


def _ancien_exact(conn, code_insee, numero, rep, nom_voie) -> tuple:
    """Same exact-identity rule as eligibilite_adresse_exacte, on old rows."""
    from app.connectivite.queries import _norme

    lignes = conn.execute(
        _ANCIENS_CANDIDATS,
        {"code_insee": code_insee, "numero": numero, "nom_voie": nom_voie},
    ).fetchall()
    if not lignes:
        return ("aucune", Counter())
    exactes = [
        ligne
        for ligne in lignes
        if _norme(ligne[1]) == _norme(nom_voie)
        and _norme(ligne[2]) == _norme(numero)
        and _norme(ligne[3]) == _norme(rep)
    ]
    if len({ligne[0] for ligne in exactes}) != 1:
        return ("indeterminee", Counter())
    return ("unique", Counter(tuple(ligne) for ligne in exactes))


def _fibre(conn) -> dict:
    return dict(
        conn.execute(
            "SELECT code_insee, fibre_pct FROM v_commune_connectivite"
        ).fetchall()
    )


def _lignes(conn) -> Counter:
    return Counter(
        tuple(row)
        for row in conn.execute(
            "SELECT id_imb, code_insee, voie, numero, complement, technologie, "
            "debit_max, operateur, code_departement FROM eligibilite_fixe"
        ).fetchall()
    )


async def _nouveaux(dsn: str) -> tuple[list, list]:
    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            par_adresse = [
                [
                    tuple(r.values())
                    for r in await eligibilite_par_adresse(session, c, v, n)
                ]
                for c, v, n in APPELS_PAR_ADRESSE
            ]
            exacts = []
            for code, numero, rep, voie in APPELS_EXACTS:
                lu = await eligibilite_adresse_exacte(
                    session, code, numero=numero, rep=rep, nom_voie=voie
                )
                exacts.append(
                    (lu.etat, Counter(tuple(ligne.values()) for ligne in lu.lignes))
                )
    finally:
        await engine.dispose()
    return par_adresse, exacts


def _sequence(lignes: list[tuple]) -> list[tuple]:
    return [(ligne[4], ligne[5]) for ligne in lignes]


def test_routes_and_fibre_are_served_identically_and_downgrade_is_lossless(
    isolated_pg_dsn,
):
    dsn = isolated_pg_dsn
    _alembic(dsn, "upgrade", AVANT)
    with psycopg.connect(dsn) as conn:
        for ligne in LIGNES:
            conn.execute(
                "INSERT INTO eligibilite_fixe (id_imb, code_insee, voie, numero, "
                "complement, technologie, debit_max, operateur, code_departement) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '44')",
                ligne,
            )
        conn.execute(
            "INSERT INTO etl_publications (generation, dataset_name, table_name, "
            "status, relation_oids) VALUES (gen_random_uuid(), 'connectivite', "
            "'eligibilite_fixe', 'published', jsonb_build_object("
            "'eligibilite_fixe', to_regclass('eligibilite_fixe')::oid::bigint))"
        )
        # BAN keys of the equivalence sample (scripts/eligibilite_equivalence.py).
        for i, (ban_id, insee, voie, numero) in enumerate(
            (
                ("44109_0001_00012", COMMUNE, "Rue des Lilas", "12"),
                ("44109_0002_00004", COMMUNE, "Rue du Port", "4"),
                ("44020_0003_00001", AUTRE, "Place du Marche", "1"),
            )
        ):
            conn.execute(
                "INSERT INTO adresses (id, code_departement, ban_id, numero, "
                "nom_voie, code_postal, code_commune, nom_commune, position) "
                "VALUES (%s, '44', %s, %s, %s, '44000', %s, 'N', "
                "ST_SetSRID(ST_MakePoint(-1.5, 47.2), 4326))",
                (96_000_001 + i, ban_id, numero, voie, insee),
            )
        conn.execute("REFRESH MATERIALIZED VIEW v_commune_connectivite")
        conn.commit()
        avant_lignes = _lignes(conn)
        avant_fibre = _fibre(conn)
        avant_par_adresse = [
            _ancien_par_adresse(conn, *appel) for appel in APPELS_PAR_ADRESSE
        ]
        avant_exacts = [_ancien_exact(conn, *appel) for appel in APPELS_EXACTS]
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        empreintes_avant = exporter(conn, ["44"], "graine", 10)
    assert empreintes_avant["forme"] == "par_offre"
    assert empreintes_avant["cles"] == 3
    assert {k: float(v) for k, v in avant_fibre.items()} == {COMMUNE: 75.0, AUTRE: 0.0}

    _alembic(dsn, "upgrade", APRES)
    assert (
        ScriptDirectory.from_config(_config(dsn)).get_revision(APRES).down_revision
        == AVANT
    )
    with psycopg.connect(dsn) as conn:
        immeubles = conn.execute(
            "SELECT id_imb, voie, complement, cardinality(offres) "
            "FROM eligibilite_fixe ORDER BY 1, 2"
        ).fetchall()
        assert immeubles == [
            ("IMB-A", "RUE DES LILAS", None, 5),  # duplicate offer kept
            ("IMB-B1", "RUE DES LILAS", "BIS", 1),
            ("IMB-B2", "RUE DES LILAS", "BIS", 1),
            ("IMB-C", "RUE DU PORT", None, 1),
            ("IMB-C", "RUE DU PORT PROLONGEE", None, 1),
            ("IMB-D", "PLACE DU MARCHE", None, 1),
        ]
        assert _fibre(conn) == avant_fibre
        ancrage = conn.execute(
            "SELECT (relation_oids->>'eligibilite_fixe')::oid "
            "= to_regclass('eligibilite_fixe')::oid FROM etl_publications "
            "WHERE dataset_name = 'connectivite'"
        ).fetchone()[0]
        assert ancrage is True

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        empreintes_apres = exporter(conn, ["44"], "graine", 10)
    assert empreintes_apres["forme"] == "par_immeuble"
    assert comparer(empreintes_avant, empreintes_apres)["differentes"] == []

    apres_par_adresse, apres_exacts = asyncio.run(_nouveaux(dsn))
    for avant, apres in zip(avant_par_adresse, apres_par_adresse, strict=True):
        assert Counter(apres) == Counter(avant)
        assert _sequence(apres) == _sequence(avant)
    assert apres_exacts == avant_exacts
    assert [etat for etat, _ in apres_exacts] == [
        "unique",
        "indeterminee",
        "unique",
        "aucune",
    ]

    _alembic(dsn, "downgrade", AVANT)
    with psycopg.connect(dsn) as conn:
        assert _lignes(conn) == avant_lignes
        assert _fibre(conn) == avant_fibre
        assert conn.execute("SELECT to_regclass('eligibilite_offres')").fetchone() == (
            None,
        )
        assert (
            conn.execute(
                "SELECT (relation_oids->>'eligibilite_fixe')::oid "
                "= to_regclass('eligibilite_fixe')::oid FROM etl_publications "
                "WHERE dataset_name = 'connectivite'"
            ).fetchone()[0]
            is True
        )
    _alembic(dsn, "upgrade", APRES)
