#!/usr/bin/env python3
"""Cost of the D-02 deduplication of DVF locals (plan 04.4-01, task 3).

Since 2026-10-21 the rule measured is variant V of D-02 (Ianis): the
multiplicity of a local is the max over the pairs (parcel, culture) of its
lines, in A as in B.

Read-only: writes nothing, prints one JSON report on stdout. Aggregates only,
never a mutation id, an address or a parcel (T-04.4-04).

Two modes:

- ``sql``: on the work base, through the read-only connection of the tool
  (``DATABASE_URL`` of ``wb-tool.sh --read-only``, role ``wb_reader``). Runs
  three variants of the reconstruction aggregation on the same served rows,
  each read by a named cursor in batches of 5 000 (``_BATCH_SIZE`` of the
  reconstruction) and followed by its Python work per mutation:

  - ``AVANT``: ``_AGGREGATION_SQL`` at the starting SHA of the phase
    (1ba38b78, 2026-10-21), locals counted by ``count(*) FILTER``;
  - ``A``: deduplication in SQL (group by signature, parcel and culture,
    then max);
  - ``B``: the served rule, ``reconstruction._AGGREGATION_SQL`` (copied here,
    equality checked by a test), locals counted by ``compte_locaux``.

  Duration, rows, mutations and ``tracemalloc`` peak of each.

- ``national``: on the hypervisor, standard library only. Reads the CSV files of an
  archived DVF run, groups the lines by mutation and evaluates
  ``compte_lignes`` then ``qualifie_mutation`` in as many passes as needed to
  reach 3 100 000 mutations (national order of magnitude, docstring T-57-19),
  with the naive counting of before (one local per line) as control. CPU time
  and peak memory.

The qualification functions come from ``app/etl_dvf/qualification.py``
concatenated before this script when it is sent on standard input (guard on
``globals()``), else from the package import. To stay concatenable the script
has no ``from __future__`` and the ``national`` mode imports the standard
library only (``psycopg`` only in the ``sql`` mode, imported at call time)::

    cat api/backend/app/etl_dvf/qualification.py \\
        api/backend/scripts/mesure_dvf_dedup_cout.py \\
      | ssh <hypervisor> python3 - national --run 48-20261019T171140Z
    cat api/backend/app/etl_dvf/qualification.py \\
        api/backend/scripts/mesure_dvf_dedup_cout.py \\
      | wb-tool.sh --read-only python - sql
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
import tracemalloc
from collections import Counter, defaultdict
from decimal import Decimal

if "compte_locaux" not in globals():  # run as a module, not concatenated
    from app.etl_dvf.qualification import (
        CompteursMutation,
        compte_lignes,
        compte_locaux,
        qualifie_mutation,
    )

CIBLE_NATIONALE = 3_100_000
BATCH_SIZE = 5_000
ARCHIVE_DVF = "/mnt/usb-8tb/datacommune-sources/dvf"
DEPARTEMENTS = ("44", "85")

# Copied from app/etl_dvf/reconstruction.py at 1ba38b78 (2026-10-21), the
# starting SHA of phase 04.4: locals counted per line in SQL.
AGGREGATION_AVANT_SQL = """
    SELECT
        code_departement,
        id_mutation,
        min(date_mutation) AS date_mutation,
        max(nature_mutation) AS nature_mutation,
        max(valeur_fonciere) AS valeur_fonciere,
        (count(DISTINCT valeur_fonciere) > 1) AS valeur_divergente,
        count(*) FILTER (WHERE code_type_local = '1') AS nb_locaux_maison,
        count(*) FILTER (WHERE code_type_local = '2')
            AS nb_locaux_appartement,
        count(*) FILTER (WHERE code_type_local = '4')
            AS nb_locaux_commercial,
        count(*) FILTER (WHERE code_type_local = '3') AS nb_locaux_dependance,
        count(*) AS nb_lignes,
        count(DISTINCT numero_disposition) AS nb_dispositions,
        count(DISTINCT code_commune) AS nb_communes,
        count(DISTINCT id_parcelle) AS nb_parcelles,
        sum(surface_reelle_bati) AS surface_bati_totale,
        sum(surface_terrain) AS surface_terrain_totale,
        sum(nombre_pieces_principales) AS nombre_pieces_totales,
        min(code_commune) AS code_commune,
        (array_agg(nom_commune ORDER BY id))[1] AS nom_commune,
        (array_agg(adresse_numero ORDER BY id))[1] AS adresse_numero,
        (array_agg(adresse_suffixe ORDER BY id))[1] AS adresse_suffixe,
        (array_agg(adresse_nom_voie ORDER BY id))[1] AS adresse_nom_voie,
        (array_agg(code_postal ORDER BY id))[1] AS code_postal,
        ST_Centroid(ST_Collect(position)) AS position
    FROM mutations
    WHERE code_departement = ANY(%(departements)s)
    GROUP BY code_departement, id_mutation
"""

_LIGNE_LOCAL = (
    "code_type_local IS NOT NULL OR surface_reelle_bati IS NOT NULL "
    "OR nombre_pieces_principales IS NOT NULL"
)

# Lines of variant B, as app.etl_dvf.reconstruction._LIGNE_LOCAL since IN-05
# (bare land lines carry the land surface); A and AVANT keep _LIGNE_LOCAL.
_LIGNE_COMPTEE = f"{_LIGNE_LOCAL} OR surface_terrain IS NOT NULL"

# Equal to app.etl_dvf.reconstruction._AGGREGATION_SQL (test
# tests/test_mesure_dvf_dedup_cout.py): the variant measured is the served one.
AGGREGATION_B_SQL = f"""
    SELECT
        code_departement,
        id_mutation,
        min(date_mutation) AS date_mutation,
        max(nature_mutation) AS nature_mutation,
        max(valeur_fonciere) AS valeur_fonciere,
        (count(DISTINCT valeur_fonciere) > 1) AS valeur_divergente,
        count(*) AS nb_lignes,
        count(DISTINCT numero_disposition) AS nb_dispositions,
        count(DISTINCT code_commune) AS nb_communes,
        count(DISTINCT id_parcelle) AS nb_parcelles,
        array_agg(numero_disposition ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_disposition,
        array_agg(code_type_local ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_type,
        array_agg(surface_reelle_bati ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_surface,
        array_agg(nombre_pieces_principales ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_pieces,
        array_agg(id_parcelle ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_parcelle,
        array_agg(code_nature_culture ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_culture,
        array_agg(surface_terrain ORDER BY id)
            FILTER (WHERE {_LIGNE_COMPTEE}) AS locaux_terrain,
        min(code_commune) AS code_commune,
        (array_agg(nom_commune ORDER BY code_commune, id))[1] AS nom_commune,
        (array_agg(adresse_numero ORDER BY code_commune, id))[1]
            AS adresse_numero,
        (array_agg(adresse_suffixe ORDER BY code_commune, id))[1]
            AS adresse_suffixe,
        (array_agg(adresse_nom_voie ORDER BY code_commune, id))[1]
            AS adresse_nom_voie,
        (array_agg(code_postal ORDER BY code_commune, id))[1] AS code_postal,
        ST_Centroid(ST_Collect(position)) AS position
    FROM mutations
    WHERE code_departement = ANY(%(departements)s)
    GROUP BY code_departement, id_mutation
"""

# Variant A: the D-02 rule (variant V) in SQL. Multiplicity of a signature =
# max over its (parcel, culture) pairs (NULL a value like any other: GROUP BY
# groups NULLs) of its lines.
AGGREGATION_A_SQL = f"""
    WITH par_parcelle AS (
        SELECT code_departement, id_mutation, numero_disposition,
               code_type_local, surface_reelle_bati,
               nombre_pieces_principales, count(*) AS n
        FROM mutations
        WHERE code_departement = ANY(%(departements)s) AND ({_LIGNE_LOCAL})
        GROUP BY code_departement, id_mutation, numero_disposition,
                 code_type_local, surface_reelle_bati,
                 nombre_pieces_principales, id_parcelle, code_nature_culture
    ), signatures AS (
        SELECT code_departement, id_mutation, code_type_local,
               surface_reelle_bati, nombre_pieces_principales, max(n) AS m
        FROM par_parcelle
        GROUP BY code_departement, id_mutation, numero_disposition,
                 code_type_local, surface_reelle_bati,
                 nombre_pieces_principales
    ), locaux AS (
        SELECT code_departement, id_mutation,
               coalesce(sum(m) FILTER (WHERE code_type_local = '1'), 0)
                   AS nb_locaux_maison,
               coalesce(sum(m) FILTER (WHERE code_type_local = '2'), 0)
                   AS nb_locaux_appartement,
               coalesce(sum(m) FILTER (WHERE code_type_local = '4'), 0)
                   AS nb_locaux_commercial,
               coalesce(sum(m) FILTER (WHERE code_type_local = '3'), 0)
                   AS nb_locaux_dependance,
               sum(surface_reelle_bati * m) AS surface_bati_totale,
               sum(nombre_pieces_principales * m) AS nombre_pieces_totales
        FROM signatures
        GROUP BY code_departement, id_mutation
    ), base AS (
        SELECT
            code_departement,
            id_mutation,
            min(date_mutation) AS date_mutation,
            max(nature_mutation) AS nature_mutation,
            max(valeur_fonciere) AS valeur_fonciere,
            (count(DISTINCT valeur_fonciere) > 1) AS valeur_divergente,
            count(*) AS nb_lignes,
            count(DISTINCT numero_disposition) AS nb_dispositions,
            count(DISTINCT code_commune) AS nb_communes,
            count(DISTINCT id_parcelle) AS nb_parcelles,
            sum(surface_terrain) AS surface_terrain_totale,
            min(code_commune) AS code_commune,
            (array_agg(nom_commune ORDER BY code_commune, id))[1] AS nom_commune,
            (array_agg(adresse_numero ORDER BY code_commune, id))[1]
                AS adresse_numero,
            (array_agg(adresse_suffixe ORDER BY code_commune, id))[1]
                AS adresse_suffixe,
            (array_agg(adresse_nom_voie ORDER BY code_commune, id))[1]
                AS adresse_nom_voie,
            (array_agg(code_postal ORDER BY code_commune, id))[1]
                AS code_postal,
            ST_Centroid(ST_Collect(position)) AS position
        FROM mutations
        WHERE code_departement = ANY(%(departements)s)
        GROUP BY code_departement, id_mutation
    )
    SELECT base.*,
           coalesce(l.nb_locaux_maison, 0) AS nb_locaux_maison,
           coalesce(l.nb_locaux_appartement, 0) AS nb_locaux_appartement,
           coalesce(l.nb_locaux_commercial, 0) AS nb_locaux_commercial,
           coalesce(l.nb_locaux_dependance, 0) AS nb_locaux_dependance,
           l.surface_bati_totale, l.nombre_pieces_totales
    FROM base
    LEFT JOIN locaux l USING (code_departement, id_mutation)
"""

# The decision rule is written in mesures/dedup-cout.json before the first
# measurement; `B_dans_la_regle` below only applies its two thresholds
# (overhead <= 25 % or <= 600 s, Python peak <= 2x).

# -- Python work per mutation -------------------------------------------------


def _compteurs_comptes(ligne):
    """AVANT and A: the SQL row already carries the six local counters."""
    return CompteursMutation(
        nb_locaux_maison=ligne["nb_locaux_maison"],
        nb_locaux_appartement=ligne["nb_locaux_appartement"],
        nb_locaux_commercial=ligne["nb_locaux_commercial"],
        nb_locaux_dependance=ligne["nb_locaux_dependance"],
        nb_lignes=ligne["nb_lignes"],
        nb_dispositions=ligne["nb_dispositions"],
        nb_communes=ligne["nb_communes"],
        nb_parcelles=ligne["nb_parcelles"],
        surface_bati_totale=ligne["surface_bati_totale"],
        surface_terrain_totale=ligne["surface_terrain_totale"],
        nombre_pieces_totales=ligne["nombre_pieces_totales"],
        nature_mutation=ligne["nature_mutation"],
        valeur_fonciere=ligne["valeur_fonciere"],
        valeur_divergente=ligne["valeur_divergente"],
    )


def _compteurs_tableaux(ligne):
    """B: same reshape as reconstruction._compteurs_depuis_ligne."""
    locaux = compte_locaux(
        zip(
            ligne["locaux_disposition"] or (),
            ligne["locaux_type"] or (),
            ligne["locaux_surface"] or (),
            ligne["locaux_pieces"] or (),
            ligne["locaux_parcelle"] or (),
            ligne["locaux_culture"] or (),
            ligne["locaux_terrain"] or (),
            strict=True,
        )
    )
    return CompteursMutation(
        nb_locaux_maison=locaux.nb_locaux_maison,
        nb_locaux_appartement=locaux.nb_locaux_appartement,
        nb_locaux_commercial=locaux.nb_locaux_commercial,
        nb_locaux_dependance=locaux.nb_locaux_dependance,
        nb_lignes=ligne["nb_lignes"],
        nb_dispositions=ligne["nb_dispositions"],
        nb_communes=ligne["nb_communes"],
        nb_parcelles=ligne["nb_parcelles"],
        surface_bati_totale=locaux.surface_bati_totale,
        surface_terrain_totale=locaux.surface_terrain_totale,
        nombre_pieces_totales=locaux.nombre_pieces_totales,
        nature_mutation=ligne["nature_mutation"],
        valeur_fonciere=ligne["valeur_fonciere"],
        valeur_divergente=ligne["valeur_divergente"],
    )


VARIANTES_SQL = {
    "AVANT": (AGGREGATION_AVANT_SQL, _compteurs_comptes),
    "A": (AGGREGATION_A_SQL, _compteurs_comptes),
    "B": (AGGREGATION_B_SQL, _compteurs_tableaux),
}


def _compte_naif(lignes):
    """Control of the national mode: ``compte_lignes`` at 1ba38b78, one local
    per line (before D-02), same other counters, no log."""
    lignes = list(lignes)

    def distincts(champ):
        return len({ligne[champ] for ligne in lignes if ligne.get(champ)})

    def somme(champ):
        valeurs = [ligne.get(champ) for ligne in lignes]
        presentes = [v for v in valeurs if v is not None]
        return sum(presentes) if presentes else None

    par_type = Counter(ligne.get("code_type_local") for ligne in lignes)
    valeurs = {
        ligne.get("valeur_fonciere") for ligne in lignes if ligne.get("valeur_fonciere")
    }
    nature = next(
        (ligne["nature_mutation"] for ligne in lignes if ligne.get("nature_mutation")),
        None,
    )
    return CompteursMutation(
        nb_locaux_maison=par_type["1"],
        nb_locaux_appartement=par_type["2"],
        nb_locaux_commercial=par_type["4"],
        nb_locaux_dependance=par_type["3"],
        nb_lignes=len(lignes),
        nb_dispositions=distincts("numero_disposition"),
        nb_communes=distincts("code_commune"),
        nb_parcelles=distincts("id_parcelle"),
        surface_bati_totale=somme("surface_reelle_bati"),
        surface_terrain_totale=somme("surface_terrain"),
        nombre_pieces_totales=somme("nombre_pieces_principales"),
        nature_mutation=nature,
        valeur_fonciere=max(valeurs) if valeurs else None,
        valeur_divergente=len(valeurs) > 1,
    )


# -- sql mode -------------------------------------------------------------------


def _dsn():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL absent: run through wb-tool.sh --read-only")
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def _passe_sql(conn, sql, travail, departements, memoire):
    from psycopg.rows import dict_row

    if memoire:
        tracemalloc.start()
    debut, cpu = time.perf_counter(), time.process_time()
    mutations = lignes = 0
    classes = Counter()
    with (
        conn.transaction(),
        conn.cursor(name="mesure_dedup", row_factory=dict_row) as cur,
    ):
        cur.execute(sql, {"departements": list(departements)})
        while True:
            lot = cur.fetchmany(BATCH_SIZE)
            if not lot:
                break
            for ligne in lot:
                resultat = qualifie_mutation(travail(ligne))
                mutations += 1
                lignes += ligne["nb_lignes"]
                classes[resultat.classe_mutation] += 1
    mesure = {
        "duree_s": round(time.perf_counter() - debut, 3),
        "cpu_python_s": round(time.process_time() - cpu, 3),
        "mutations": mutations,
        "lignes": lignes,
        "classes": dict(sorted(classes.items())),
    }
    if memoire:
        mesure["pic_tracemalloc_octets"] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return mesure


def mode_sql(departements):
    import psycopg

    # Every pass runs in its own READ ONLY transaction (the role wb_reader is
    # read-only at role level too): this script cannot write.
    with psycopg.connect(_dsn(), autocommit=True) as conn:
        conn.read_only = True
        version = conn.execute("SHOW server_version").fetchone()[0]
        mesures = {}
        for nom, (sql, travail) in VARIANTES_SQL.items():
            temps = _passe_sql(conn, sql, travail, departements, memoire=False)
            pic = _passe_sql(conn, sql, travail, departements, memoire=True)
            temps["pic_tracemalloc_octets"] = pic["pic_tracemalloc_octets"]
            temps["duree_passe_tracemalloc_s"] = pic["duree_s"]
            mesures[nom] = temps
    avant, b = mesures["AVANT"], mesures["B"]
    facteur = CIBLE_NATIONALE / avant["mutations"] if avant["mutations"] else 0
    projection = {
        "etiquette": "deduite (mesure x 3 100 000 / mutations mesurees)",
        "facteur": round(facteur, 3),
        **{
            f"duree_{nom}_s": round(m["duree_s"] * facteur, 1)
            for nom, m in mesures.items()
        },
    }
    surcout_s = projection["duree_B_s"] - projection["duree_AVANT_s"]
    surcout_pct = 100 * (b["duree_s"] / avant["duree_s"] - 1) if avant["duree_s"] else 0
    ratio_pic = (
        b["pic_tracemalloc_octets"] / avant["pic_tracemalloc_octets"]
        if avant["pic_tracemalloc_octets"]
        else 0
    )
    projection.update(
        surcout_B_s=round(surcout_s, 1),
        surcout_B_pct=round(surcout_pct, 1),
        ratio_pic_B_sur_AVANT=round(ratio_pic, 2),
        B_dans_la_regle=bool(
            (surcout_pct <= 25 or surcout_s <= 600) and ratio_pic <= 2
        ),
    )
    return {
        "mode": "sql",
        "departements": list(departements),
        "server_version": version,
        "lot": BATCH_SIZE,
        "mesures": mesures,
        "projection_nationale": projection,
    }


# -- national mode --------------------------------------------------------------


def _nombre(valeur, decimal=False):
    valeur = (valeur or "").strip().replace(",", ".")
    if not valeur:
        return None
    try:
        return Decimal(valeur) if decimal else int(float(valeur))
    except (ValueError, ArithmeticError):
        return None


def _departement(code):
    if not code or len(code) < 2:
        return "00"
    return code[:3] if code.startswith("97") and len(code) >= 3 else code[:2]


def _ligne_archive(row):
    return {
        "numero_disposition": row.get("numero_disposition") or None,
        "code_type_local": (row.get("code_type_local") or "").strip() or None,
        "surface_reelle_bati": _nombre(row.get("surface_reelle_bati")),
        "nombre_pieces_principales": _nombre(row.get("nombre_pieces_principales")),
        "surface_terrain": _nombre(row.get("surface_terrain")),
        "id_parcelle": row.get("id_parcelle") or None,
        "code_nature_culture": row.get("code_nature_culture") or None,
        "code_commune": row.get("code_commune") or None,
        "nature_mutation": row.get("nature_mutation") or None,
        "valeur_fonciere": _nombre(row.get("valeur_fonciere"), decimal=True),
    }


def _charge_archive(racine, run):
    with open(f"{racine}/runs/{run}.json", encoding="utf-8") as handle:
        record = json.load(handle)
    mutations = defaultdict(list)
    lignes = 0
    for fichier in record["files"]:
        chemin = f"{racine}/{fichier['object']}"
        with open(chemin, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                lignes += 1
                cle = (_departement(row.get("code_commune")), row["id_mutation"])
                mutations[cle].append(_ligne_archive(row))
    return record, list(mutations.values()), lignes


def _passes_nationales(groupes, compte, cible):
    passes = max(1, -(-cible // len(groupes))) if groupes else 0
    classes = Counter()
    cpu = time.process_time()
    for _ in range(passes):
        for lignes in groupes:
            classes[qualifie_mutation(compte(lignes)).classe_mutation] += 1
    duree = time.process_time() - cpu
    tracemalloc.start()
    for lignes in groupes:
        qualifie_mutation(compte(lignes))
    pic = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return {
        "passes": passes,
        "mutations_evaluees": passes * len(groupes),
        "cpu_s": round(duree, 3),
        "pic_tracemalloc_octets_une_passe": pic,
        "classes_une_passe": {k: v // passes for k, v in sorted(classes.items())},
    }


def mode_national(racine, run, cible):
    record, groupes, lignes = _charge_archive(racine, run)
    # Silence the divergence warnings (one per mutation and pass) for the
    # timed passes only; the caller's logging is restored afterwards.
    precedent = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        temoin = _passes_nationales(groupes, _compte_naif, cible)
        b = _passes_nationales(groupes, compte_lignes, cible)
    finally:
        logging.disable(precedent)
    surcout_s = b["cpu_s"] - temoin["cpu_s"]
    return {
        "mode": "national",
        "run": run,
        "fichiers": [f["relpath"] for f in record["files"]],
        "lignes": lignes,
        "mutations": len(groupes),
        "cible": cible,
        "python": sys.version.split()[0],
        "mesures": {"temoin_naif": temoin, "B_compte_lignes": b},
        "partie_python_nationale": {
            "etiquette": "mesuree au volume (passes repetees sur l'archive)",
            "surcout_B_s": round(surcout_s, 3),
            "surcout_B_pct": round(100 * surcout_s / temoin["cpu_s"], 1)
            if temoin["cpu_s"]
            else None,
            "ratio_pic_B_sur_temoin": round(
                b["pic_tracemalloc_octets_une_passe"]
                / temoin["pic_tracemalloc_octets_une_passe"],
                2,
            )
            if temoin["pic_tracemalloc_octets_une_passe"]
            else None,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modes = parser.add_subparsers(dest="mode", required=True)
    sql = modes.add_parser("sql")
    sql.add_argument("--departements", default=",".join(DEPARTEMENTS))
    national = modes.add_parser("national")
    national.add_argument("--run", required=True)
    national.add_argument("--racine", default=ARCHIVE_DVF)
    national.add_argument("--cible", type=int, default=CIBLE_NATIONALE)
    args = parser.parse_args(argv)
    if args.mode == "sql":
        rapport = mode_sql(tuple(args.departements.split(",")))
    else:
        rapport = mode_national(args.racine, args.run, args.cible)
    print(json.dumps(rapport, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
