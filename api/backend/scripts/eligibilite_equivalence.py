#!/usr/bin/env python3
"""Equivalence fingerprints of the broadband reads, before and after 04.1-16.

``export`` reads, for a fixed sample of BAN addresses, what the two served
queries of ``app.connectivite.queries`` return, in whichever shape
``eligibilite_fixe`` has (one row per offer before migration 106, one row
per building with ``offres`` after: detected by the column), and writes one
SHA-256 per address of the canonical answer, plus one per commune of
``fibre_pct``. ``compare`` says which fingerprints differ and exits 1 when
any does. No address, value nor identifier is written: an address is named
by the SHA-256 of its BAN key.

Canonical answer of an address:
- ``par_adresse`` (``eligibilite_par_adresse(code_insee, nom_voie, numero)``):
  the sorted multiset of its rows and the sequence of (technology, speed);
- ``candidats`` (rows ``eligibilite_adresse_exacte`` decides on): the sorted
  multiset of the candidate rows of the number and street.

Sample: ``--nombre`` BAN addresses of the departements, ordered by
``md5(seed || ban_id)`` (deterministic, independent of the physical order).

Launch on the work base, read-only (before: work base at revision < 106)::

    wb-tool.sh --read-only python - export --seed 20261014 \\
        --out - < api/backend/scripts/eligibilite_equivalence.py

Every statement goes through ``assert_select_only`` on a connection opened by
``_open_read_only_connection`` (``verify_golden_cases``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.verify_golden_cases import (
    GoldenSecurityError,
    _open_read_only_connection,
    assert_select_only,
)

DEPARTEMENTS_DEFAUT: tuple[str, ...] = ("44", "85")
NOMBRE_DEFAUT = 10_000

FORME_SQL = """
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'eligibilite_fixe'
      AND column_name = 'offres'
) AS par_immeuble
"""
ECHANTILLON_SQL = """
SELECT a.ban_id, a.code_commune, a.nom_voie, a.numero
FROM adresses a
WHERE a.code_departement = ANY(%(departements)s)
  AND a.numero IS NOT NULL
ORDER BY md5(%(seed)s || a.ban_id)
LIMIT %(nombre)s
"""
# One row per offer, in both shapes: the same columns, the same filters.
_OFFRES_PAR_LIGNE = "eligibilite_fixe e"
_OFFRES_PAR_IMMEUBLE = (
    "eligibilite_fixe e CROSS JOIN LATERAL unnest(e.offres) AS u(code) "
    "JOIN eligibilite_offres o ON o.code = u.code"
)
# Same tie-break as the served query: without it, an address of more than 50
# offers keeps an arbitrary subset under LIMIT and differs between two reads.
_PAR_ADRESSE = """
SELECT e.id_imb, e.code_insee, e.voie, e.numero, {t}.technologie,
       {t}.debit_max, {t}.operateur
FROM {source}
WHERE e.code_insee = %(code_insee)s
  AND unaccent(lower(e.voie)) LIKE '%%' || unaccent(lower(%(nom_voie)s)) || '%%'
  AND e.numero = %(numero)s
ORDER BY {t}.technologie, {t}.debit_max DESC NULLS LAST, {t}.operateur, e.id_imb
LIMIT 50
"""
_CANDIDATS = """
SELECT e.id_imb, e.voie, e.numero, e.complement, {t}.technologie, {t}.debit_max
FROM {source}
WHERE e.code_insee = %(code_insee)s AND e.numero = %(numero)s
  AND unaccent(lower(e.voie)) LIKE '%%' || unaccent(lower(%(nom_voie)s)) || '%%'
"""
_FIBRE = "SELECT code_insee, fibre_pct FROM v_commune_connectivite"
REQUETES = {
    False: {
        "par_adresse": _PAR_ADRESSE.format(source=_OFFRES_PAR_LIGNE, t="e"),
        "candidats": _CANDIDATS.format(source=_OFFRES_PAR_LIGNE, t="e"),
    },
    True: {
        "par_adresse": _PAR_ADRESSE.format(source=_OFFRES_PAR_IMMEUBLE, t="o"),
        "candidats": _CANDIDATS.format(source=_OFFRES_PAR_IMMEUBLE, t="o"),
    },
}
ALLOWED_SQL = frozenset(
    {
        FORME_SQL,
        ECHANTILLON_SQL,
        _FIBRE,
        *(sql for par_forme in REQUETES.values() for sql in par_forme.values()),
    }
)


def _sha(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _canon(valeur: Any) -> Any:
    return None if valeur is None else str(valeur)


def empreinte_par_adresse(lignes: Sequence[Sequence[Any]]) -> str:
    """Sorted multiset of the rows and sequence of (technology, speed)."""
    canon = [[_canon(v) for v in ligne] for ligne in lignes]
    sequence = [[ligne[4], ligne[5]] for ligne in canon]
    return _sha(json.dumps([sorted(canon, key=json.dumps), sequence]))


def empreinte_multiensemble(lignes: Iterable[Sequence[Any]]) -> str:
    canon = [[_canon(v) for v in ligne] for ligne in lignes]
    return _sha(json.dumps(sorted(canon, key=json.dumps)))


def _executer(conn: Any, sql: str, params: Mapping[str, Any]) -> list[tuple]:
    assert_select_only(sql, ALLOWED_SQL)
    return [tuple(ligne.values()) for ligne in conn.execute(sql, dict(params))]


def exporter(
    conn: Any, departements: Sequence[str], seed: str, nombre: int
) -> dict[str, Any]:
    par_immeuble = bool(_executer(conn, FORME_SQL, {})[0][0])
    requetes = REQUETES[par_immeuble]
    cles = _executer(
        conn,
        ECHANTILLON_SQL,
        {"departements": list(departements), "seed": seed, "nombre": nombre},
    )
    adresses = {}
    for ban_id, code_insee, nom_voie, numero in cles:
        params = {"code_insee": code_insee, "nom_voie": nom_voie, "numero": numero}
        adresses[_sha(ban_id)] = {
            "par_adresse": empreinte_par_adresse(
                _executer(conn, requetes["par_adresse"], params)
            ),
            "candidats": empreinte_multiensemble(
                _executer(conn, requetes["candidats"], params)
            ),
        }
    communes = {
        _sha(code): _sha(json.dumps(_canon(pct)))
        for code, pct in _executer(conn, _FIBRE, {})
    }
    return {
        "forme": "par_immeuble" if par_immeuble else "par_offre",
        "seed": seed,
        "departements": list(departements),
        "cles": len(adresses),
        "adresses": adresses,
        "communes_fibre": communes,
    }


def comparer(avant: Mapping[str, Any], apres: Mapping[str, Any]) -> dict[str, Any]:
    """Fingerprints of the same keys; a key missing on one side differs."""
    differentes = []
    for famille in ("adresses", "communes_fibre"):
        cles = set(avant[famille]) | set(apres[famille])
        differentes += [
            f"{famille}:{cle}"
            for cle in sorted(cles)
            if avant[famille].get(cle) != apres[famille].get(cle)
        ]
    total = len(set(avant["adresses"]) | set(apres["adresses"])) + len(
        set(avant["communes_fibre"]) | set(apres["communes_fibre"])
    )
    return {
        "meme_echantillon": avant["seed"] == apres["seed"]
        and avant["departements"] == apres["departements"],
        "egales": total - len(differentes),
        "differentes": differentes,
    }


def _sha_script() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return os.getenv("SCRIPT_SHA256")


def _ecrire(rapport: Mapping[str, Any], sortie: str) -> None:
    texte = json.dumps(rapport, ensure_ascii=False, indent=2, sort_keys=True)
    if sortie == "-":
        print(texte)
    else:
        Path(sortie).write_text(texte + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = parser.add_subparsers(dest="commande", required=True)
    export = sous.add_parser("export")
    export.add_argument("--seed", required=True)
    export.add_argument("--nombre", type=int, default=NOMBRE_DEFAUT)
    export.add_argument(
        "--departements",
        type=lambda v: [d.strip() for d in v.split(",") if d.strip()],
        default=list(DEPARTEMENTS_DEFAUT),
    )
    export.add_argument("--out", default="-")
    compare = sous.add_parser("compare")
    compare.add_argument("--avant", required=True)
    compare.add_argument("--apres", required=True)
    args = parser.parse_args(argv)

    if args.commande == "compare":
        resultat = comparer(
            json.loads(Path(args.avant).read_text(encoding="utf-8")),
            json.loads(Path(args.apres).read_text(encoding="utf-8")),
        )
        print(json.dumps(resultat, indent=2, sort_keys=True))
        return 0 if resultat["meme_echantillon"] and not resultat["differentes"] else 1

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise GoldenSecurityError("DATABASE_URL is missing")
    conn = _open_read_only_connection(
        dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    )
    try:
        rapport = exporter(conn, args.departements, args.seed, args.nombre)
    finally:
        conn.close()
    rapport["script_sha256"] = _sha_script()
    rapport["sha_servi"] = os.getenv("WORKBASE_SHA")
    _ecrire(rapport, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
