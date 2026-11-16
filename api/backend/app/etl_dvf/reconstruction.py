"""Reconstruct `mutations_grain` and `mutation_parcelles` from `mutations`.

Phase 57 (DVF au grain mutation et attribution residentielle) -- D-01
(``mutations_grain`` is ONE ROW PER MUTATION, fully qualified), D-03
(``mutations`` remains the raw per-line source, coexisting, never dropped),
D-04 (this reconstruction runs in the SAME ETL pass as the line load -- there
is never a window where the two tables disagree), D-05 (the pure module in
``app.etl_dvf.qualification`` is the ONLY place a mutation is classified --
never a parallel SQL ``CASE``), D-08 (line-level detail stays joinable back
via ``id_mutation``, nothing here is JSONB), D-09 (``mutation_parcelles``
links a mutation to every parcel it touches). Covers DVF-01 (a price counted
once), DVF-02 (``nombre_lots`` never summed/maxed into a mutation total) and
DVF-03 (a residential local is never priced together with a commercial one).

THE BLOCKING INVARIANT (D-04): reconstruire avant de filtrer. The SQL
aggregation below groups every row of ``mutations`` by
``(code_departement, id_mutation)`` with NO ``WHERE`` clause on
``code_type_local`` -- commercial locals, terrains, dependances all
contribute to a mutation's counters exactly like residential ones. Only the
downstream CONSUMERS (matview, ``/v1/dvf``) filter afterwards on
``classe_mutation``. Reconstructing on a partial, already-filtered view of
``mutations`` would silently reintroduce the Pornichet-shaped bug this phase
is named for: a commercial local sharing an ``id_mutation`` with a house
would never be counted, and the house would wrongly look mono-local.

Since phase 04.4 (D-01) the invariant holds from the SOURCE FILE: every line
of the geo-DVF file is loaded into ``mutations``, coordinates or not (a line
without them has a NULL ``position``), so a local is never lost before this
reconstruction. The position of a mutation is the centroid of its geocoded
lines only (``ST_Collect`` ignores NULL), NULL when none is geocoded. A
local repeated on several parcels, or on the culture lines of one parcel,
counts once (D-02 variant V, ``compte_locaux``).

Sequence, mirroring (never calling -- see ``_bascule_deux_paires``)
``BasePipeline.create_staging_table`` / ``swap_tables`` for a TWO-pair swap:

1. Create ``mutations_grain_staging`` and ``mutation_parcelles_staging``
   (``LIKE ... INCLUDING ALL EXCLUDING INDEXES``, own ``id`` sequence).
2. Aggregate ``mutations`` (SQL, no classification) -> qualify each mutation
   in Python (``qualifie_mutation``, D-05) -> ``COPY`` into
   ``mutations_grain_staging``, read via a NAMED server-side cursor in
   batches (never loaded whole into memory on ~3.1M mutations -- T-57-19).
3. Populate ``mutation_parcelles_staging`` (D-09).
4. Rebuild indexes and restore PK/UNIQUE constraints on both staging tables.
5. Safety guard: abort BEFORE any ``RENAME`` if staging carries <50% of the
   current ``mutations_grain`` production row count (T-57-20).
6. Swap BOTH pairs inside ONE transaction, ONE ``commit()`` (T-57-18): either
   both derived tables become fresh together, or neither does. A reader can
   never see a fresh ``mutations_grain`` next to a stale
   ``mutation_parcelles``.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.etl.base import BasePipeline
from app.etl_dvf.config import MARQUEUR_ADRESSE_SUFFIXE
from app.etl_dvf.qualification import (
    CompteursMutation,
    ResultatQualification,
    compte_locaux,
    qualifie_mutation,
)

logger = logging.getLogger(__name__)

# Rows fetched per named-cursor round-trip -- bounds memory on ~3.1M mutations,
# never materialized whole in Python at once (T-57-19).
_BATCH_SIZE = 5_000

# Column order written to mutations_grain_staging by COPY. `prix_m2` is
# intentionally NOT a column here: migration 087 does not store it (it is
# derived at query time by later plans, D-15) -- only the closed
# classification and its qualifying flags are persisted.
_GRAIN_COLUMNS: list[str] = [
    "code_departement",
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "classe_mutation",
    "nb_locaux_maison",
    "nb_locaux_appartement",
    "nb_locaux_commercial",
    "nb_locaux_dependance",
    "nb_lignes",
    "nb_dispositions",
    "nb_communes",
    "nb_parcelles",
    "surface_bati_totale",
    "surface_terrain_totale",
    "nombre_pieces_totales",
    "mono_local",
    "est_vente",
    "hors_stats_vefa",
    "valeur_divergente",
    "code_commune",
    "nom_commune",
    "adresse_numero",
    "adresse_suffixe",
    "adresse_nom_voie",
    "code_postal",
    "position",
]

# Lines handed to `compte_locaux`. A line without type, surface, rooms nor
# land surface changes none of its counters: leaving it out only saves
# transfer, the equivalence test proves it. Bare land lines are handed over
# for the land surface (IN-05), they change no local counter.
_LIGNE_LOCAL = (
    "code_type_local IS NOT NULL OR surface_reelle_bati IS NOT NULL "
    "OR nombre_pieces_principales IS NOT NULL OR surface_terrain IS NOT NULL"
)

# The reconstruction aggregation. ONE row per (code_departement, id_mutation).
# No WHERE on code_type_local: only aggregate `FILTER (WHERE ...)` clauses
# read it, and every row of the mutation still counts in nb_lignes /
# surfaces / etc regardless of its type -- this is the blocking invariant
# (see module docstring). The locals and the land surface are NOT counted
# here: seven aligned arrays carry them to `compte_locaux` (D-02 variant V,
# the only place of the rule; `locaux_culture` lets it merge a local, or the
# land of a culture line, repeated on the culture lines of its parcel).
# `min(code_commune)` is the rattachement communal rule (D-09 docstring,
# migration 087). The five address fields are read on ONE line, the first
# (`id`) of that retained commune (phase 04.4 D-04): `ORDER BY code_commune,
# id` puts NULL codes last, so the pick is the lowest-id line of
# min(code_commune), and without any commune code the lowest-id line. Same
# cost as the former `ORDER BY id`, no window nor second pass. That line may
# lack coordinates: the address is declared, the position stays the centroid
# of the geocoded lines (D-01).
_AGGREGATION_SQL = f"""
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
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_disposition,
        array_agg(code_type_local ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_type,
        array_agg(surface_reelle_bati ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_surface,
        array_agg(nombre_pieces_principales ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_pieces,
        array_agg(id_parcelle ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_parcelle,
        array_agg(code_nature_culture ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_culture,
        array_agg(surface_terrain ORDER BY id)
            FILTER (WHERE {_LIGNE_LOCAL}) AS locaux_terrain,
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

# Index definitions mirrored from migration 087, minus the two migration 107
# dropped (planar GiST of the grain, id_mutation of the parcel links: the
# unique (id_mutation, id_parcelle) serves those reads) -- the canonical name,
# the table each is built on, and the DDL suffix after the name.
_GRAIN_INDEXES: list[tuple[str, str]] = [
    ("idx_mutations_grain_id_mutation", "ON mutations_grain_staging (id_mutation)"),
    ("idx_mutations_grain_code_commune", "ON mutations_grain_staging (code_commune)"),
    ("idx_mutations_grain_date", "ON mutations_grain_staging (date_mutation)"),
    (
        "idx_mutations_grain_position_geog",
        "ON mutations_grain_staging USING GIST (geography(position))",
    ),
]
_PARCELLES_INDEXES: list[tuple[str, str]] = [
    (
        "idx_mutation_parcelles_id_parcelle",
        "ON mutation_parcelles_staging (id_parcelle)",
    ),
]


def reconstruit_mutations_grain(
    dsn: str, departements: Sequence[str]
) -> dict[str, int]:
    """Reconstruct mutations_grain + mutation_parcelles in the same pass (D-04).

    Raises on any failure. The caller (``DvfPipeline.swap()``) must NOT catch
    this: an incomplete reconstruction is worse than a failed run -- D-04's
    guarantee is that lines and mutations never diverge, which a swallowed
    exception here would silently break.
    """
    departements = list(departements)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            BasePipeline.set_statement_timeout(cur)
            _create_staging_tables(cur, departements)
        conn.commit()

        compteurs = _reconstruire_mutations(conn, dsn, departements)
        conn.commit()

        with conn.cursor() as cur:
            BasePipeline.set_statement_timeout(cur)
            liaisons = _peupler_mutation_parcelles(cur)
        conn.commit()

        with conn.cursor() as cur:
            BasePipeline.set_statement_timeout(cur)
            _create_staging_indexes(cur)
            _marquer_suffixe(cur)
            _restaurer_contraintes(cur, "mutations_grain", "mutations_grain_staging")
            _restaurer_contraintes(
                cur, "mutation_parcelles", "mutation_parcelles_staging"
            )
        conn.commit()

        with conn.cursor() as cur:
            BasePipeline.set_statement_timeout(cur)
            _garde_50_pourcent(cur, compteurs["mutations_reconstruites"])
            _bascule_deux_paires(cur, departements)
        conn.commit()

    resultat = {
        "mutations_reconstruites": compteurs["mutations_reconstruites"],
        "liaisons_parcelles": liaisons,
        "valeur_divergente_count": compteurs["valeur_divergente_count"],
    }
    logger.info(
        "Reconstruction terminee : %d mutations, %d liaisons parcelle, "
        "%d valeurs divergentes",
        resultat["mutations_reconstruites"],
        resultat["liaisons_parcelles"],
        resultat["valeur_divergente_count"],
    )
    return resultat


# -- staging table creation ---------------------------------------------------


def _create_staging_tables(cur: psycopg.Cursor, departements: Sequence[str]) -> None:
    """Create both staging tables, same doctrine as `create_staging_table`.

    `LIKE ... INCLUDING ALL EXCLUDING INDEXES` drops PK/UNIQUE (implemented as
    indexes) AND copies the production DEFAULT `nextval(...)` verbatim --
    writing to staging would silently increment the PRODUCTION sequence.
    `_own_id_sequence` detaches and reattaches a staging-owned one, exactly
    like every other pipeline's staging table (`app/etl/base.py`).
    """
    cur.execute("DROP TABLE IF EXISTS mutations_grain_staging CASCADE")
    cur.execute("DROP TABLE IF EXISTS mutation_parcelles_staging CASCADE")

    cur.execute(
        "CREATE TABLE mutations_grain_staging "
        "(LIKE mutations_grain INCLUDING ALL EXCLUDING INDEXES) "
        "PARTITION BY LIST (code_departement)"
    )
    _own_id_sequence(cur, "mutations_grain_staging")
    for dept in departements:
        safe = dept.lower()
        cur.execute(
            f"CREATE TABLE mutations_grain_staging_{safe} "
            f"PARTITION OF mutations_grain_staging FOR VALUES IN ('{dept}')"
        )
    cur.execute(
        "CREATE TABLE mutations_grain_staging_default "
        "PARTITION OF mutations_grain_staging DEFAULT"
    )

    cur.execute(
        "CREATE TABLE mutation_parcelles_staging "
        "(LIKE mutation_parcelles INCLUDING ALL EXCLUDING INDEXES)"
    )
    _own_id_sequence(cur, "mutation_parcelles_staging")


def _own_id_sequence(cur: psycopg.Cursor, staging_table: str) -> None:
    """Give a staging table its own `id` sequence (mirrors `create_staging_table`)."""
    cur.execute(f"ALTER TABLE {staging_table} ALTER COLUMN id DROP IDENTITY IF EXISTS")
    cur.execute(f"DROP SEQUENCE IF EXISTS {staging_table}_id_seq CASCADE")
    cur.execute(f"CREATE SEQUENCE {staging_table}_id_seq")
    cur.execute(
        f"ALTER TABLE {staging_table} "
        f"ALTER COLUMN id SET DEFAULT nextval('{staging_table}_id_seq')"
    )
    cur.execute(f"ALTER SEQUENCE {staging_table}_id_seq OWNED BY {staging_table}.id")


# -- aggregation, qualification, load -----------------------------------------


def _reconstruire_mutations(
    conn: psycopg.Connection, dsn: str, departements: Sequence[str]
) -> dict[str, int]:
    """Aggregate `mutations`, qualify each mutation, COPY into grain staging.

    Reads via a NAMED (server-side) cursor, batched by `_BATCH_SIZE` -- this
    is the reconstruction step T-57-19 guards against loading ~3.1M
    mutations into Python at once. `qualifie_mutation` (D-05) is invoked
    EXACTLY once per mutation, here and nowhere else in the codebase.

    The read (FETCH on the named cursor) and the write (COPY FROM STDIN) run
    on TWO SEPARATE connections. A single PostgreSQL connection cannot
    interleave a FETCH with an open COPY IN: once COPY starts, the wire
    protocol accepts only CopyData/CopyDone until the COPY ends, so a FETCH
    issued mid-COPY on the SAME connection deadlocks -- found empirically on
    the first real-database run of this module (57-07): the writer sat
    `active`/`ClientRead` waiting for more copy data forever, 0% CPU, no
    Postgres lock wait, because the FETCH was never actually sendable.
    `conn` (the caller's connection) stays reserved for the write side so
    its effect lands inside the caller's transaction, visible once
    `reconstruit_mutations_grain` commits it; the read side opens its own
    short-lived connection (a plain SELECT, nothing written on it).
    """
    reconstruites = 0
    divergentes = 0
    exemples_divergents: list[str] = []

    copy_sql = (
        "COPY mutations_grain_staging (" + ", ".join(_GRAIN_COLUMNS) + ") "
        "FROM STDIN WITH (FORMAT csv)"
    )

    with psycopg.connect(dsn) as lecture_conn:
        with lecture_conn.cursor() as reglage:
            BasePipeline.set_statement_timeout(reglage)

        with (
            lecture_conn.cursor(
                name="dvf_reconstruction_agrege", row_factory=dict_row
            ) as lecture,
            conn.cursor() as ecriture,
        ):
            lecture.execute(_AGGREGATION_SQL, {"departements": departements})

            with ecriture.copy(copy_sql) as copy:
                while True:
                    lignes = lecture.fetchmany(_BATCH_SIZE)
                    if not lignes:
                        break
                    tampon = io.StringIO()
                    writer = csv.writer(tampon)
                    for ligne in lignes:
                        compteurs = _compteurs_depuis_ligne(ligne)
                        resultat = qualifie_mutation(compteurs)
                        reconstruites += 1
                        if compteurs.valeur_divergente:
                            divergentes += 1
                            if len(exemples_divergents) < 5:
                                exemples_divergents.append(ligne["id_mutation"])
                        writer.writerow(_ligne_copy(ligne, compteurs, resultat))
                    copy.write(tampon.getvalue().encode("utf-8"))

    if divergentes:
        logger.warning(
            "%d mutations avec valeur_fonciere divergente entre dispositions (ex.: %s)",
            divergentes,
            exemples_divergents,
        )

    return {
        "mutations_reconstruites": reconstruites,
        "valeur_divergente_count": divergentes,
    }


def _compteurs_depuis_ligne(ligne: Mapping[str, Any]) -> CompteursMutation:
    """Reshape one row of the SQL aggregation into the pure module's input type.

    The SQL query above groups the lines at PostgreSQL scale; the locals and
    land surfaces it carries as seven aligned arrays are counted by
    `compte_locaux` (D-02 variant V, IN-05), the rule shared with
    `compte_lignes`. A NULL array means no such line.
    """
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


def _ligne_copy(
    ligne: Mapping[str, Any],
    compteurs: CompteursMutation,
    resultat: ResultatQualification,
) -> list[Any]:
    """One CSV row for `mutations_grain_staging`, in `_GRAIN_COLUMNS` order."""
    return [
        ligne["code_departement"],
        ligne["id_mutation"],
        ligne["date_mutation"],
        compteurs.nature_mutation,
        compteurs.valeur_fonciere,
        resultat.classe_mutation,
        compteurs.nb_locaux_maison,
        compteurs.nb_locaux_appartement,
        compteurs.nb_locaux_commercial,
        compteurs.nb_locaux_dependance,
        compteurs.nb_lignes,
        compteurs.nb_dispositions,
        compteurs.nb_communes,
        compteurs.nb_parcelles,
        compteurs.surface_bati_totale,
        compteurs.surface_terrain_totale,
        compteurs.nombre_pieces_totales,
        resultat.mono_local,
        resultat.est_vente,
        resultat.hors_stats_vefa,
        compteurs.valeur_divergente,
        ligne["code_commune"],
        ligne["nom_commune"],
        ligne["adresse_numero"],
        ligne["adresse_suffixe"],
        ligne["adresse_nom_voie"],
        ligne["code_postal"],
        ligne["position"],
    ]


def _marquer_suffixe(cur: psycopg.Cursor) -> None:
    """Mark the candidate column: this generation carries the DVF suffix (C44).

    The comment travels with the RENAME of the swap. Migration 105 adds the
    column without it, so a base migrated but not yet reimported reads as
    « suffix unknown », never as « no suffix » (plan 04.1-14). Set before
    the swap transaction, like the indexes, and only when the source
    ``mutations`` carries the mark of an import with the suffix: a rebuild
    from a source migrated but not reimported (indicator adoption) reads NULL
    suffixes, which are unknown, not absent (review 04.1-19 WR-01). The
    candidate copies the comments of the served grain (``LIKE ... INCLUDING
    ALL``): an unmarked source therefore clears the mark explicitly.
    """
    cur.execute(_MARQUEUR_SOURCE_SQL)
    ligne = cur.fetchone()
    marque = ligne is not None and ligne[0] == MARQUEUR_ADRESSE_SUFFIXE
    valeur = f"'{MARQUEUR_ADRESSE_SUFFIXE}'" if marque else "NULL"
    cur.execute(
        f"COMMENT ON COLUMN mutations_grain_staging.adresse_suffixe IS {valeur}"
    )


# The mark the DVF import leaves on the column it loaded (DvfPipeline.swap).
_MARQUEUR_SOURCE_SQL = """
    SELECT col_description(a.attrelid, a.attnum)
    FROM pg_attribute a
    WHERE a.attrelid = to_regclass('public.mutations')
      AND a.attname = 'adresse_suffixe' AND NOT a.attisdropped
"""


# -- mutation_parcelles (D-09) -------------------------------------------------


def _peupler_mutation_parcelles(cur: psycopg.Cursor) -> int:
    """D-09: one row per (mutation, parcelle) pair actually seen.

    A straight `INSERT ... SELECT DISTINCT` from `mutations` -- no counters,
    no qualification, this table only records which parcels a mutation
    touches.
    """
    cur.execute("""
        INSERT INTO mutation_parcelles_staging
            (id_mutation, id_parcelle, code_departement)
        SELECT DISTINCT id_mutation, id_parcelle, code_departement
        FROM mutations
        WHERE id_parcelle IS NOT NULL
    """)
    return cur.rowcount


# -- indexes and constraints ---------------------------------------------------


def _create_staging_indexes(cur: psycopg.Cursor) -> None:
    """Rebuild, on staging, every index migration 087 declares in production."""
    for canonical, ddl_suffix in _GRAIN_INDEXES + _PARCELLES_INDEXES:
        name = BasePipeline.staging_index_name(canonical)
        cur.execute(f"DROP INDEX IF EXISTS {name}")
        cur.execute(f"CREATE INDEX {name} {ddl_suffix}")


def _restaurer_contraintes(cur: psycopg.Cursor, production: str, staging: str) -> None:
    """Copy PK/UNIQUE constraint definitions from production to staging.

    `LIKE ... EXCLUDING INDEXES` drops PK/UNIQUE (implemented as indexes)
    along with the secondary indexes -- same doctrine as
    `BasePipeline.ensure_staging_constraints`, transposed here (not called
    directly) because this swap is two-pair and that helper is mono-pair.
    Run BEFORE the swap transaction: building a PK/UNIQUE index can be
    expensive and must not sit inside the short RENAME transaction.
    """
    source_rows = list(
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass AND contype IN ('p', 'u')
            ORDER BY contype, conname
            """,
            (production,),
        )
    )
    existing_rows = list(
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass AND contype IN ('p', 'u')
            """,
            (staging,),
        )
    )

    required = {row[0] for row in source_rows}
    present = {row[0] for row in existing_rows}
    for definition in sorted(required - present):
        cur.execute(f"ALTER TABLE {staging} ADD {definition}")


# -- safety guard ---------------------------------------------------------------


def _garde_50_pourcent(cur: psycopg.Cursor, staging_count: int) -> None:
    """Abort BEFORE any RENAME if staging carries <50% of current production.

    Same doctrine as `DvfPipeline.swap()`'s guard on `mutations` itself,
    transposed here for `mutations_grain` (T-57-20).
    """
    row = cur.execute("SELECT count(*) FROM mutations_grain").fetchone()
    old_count = row[0] if row else 0
    if old_count > 0 and staging_count < old_count * 0.5:
        msg = (
            f"Reconstruction abandonnee : staging porte {staging_count} lignes "
            f"mais mutations_grain en production en porte {old_count} "
            f"({staging_count / old_count:.0%} < seuil 50%)"
        )
        raise RuntimeError(msg)


# -- atomic two-pair swap -------------------------------------------------------


def _bascule_deux_paires(cur: psycopg.Cursor, departements: Sequence[str]) -> None:
    """Swap BOTH derived table pairs inside the transaction of the CALLER.

    This is transposed from, not a call to, `BasePipeline.swap_tables`: that
    helper is mono-pair (one production/staging name read off the pipeline's
    own properties) and commits its own transaction immediately after the
    two RENAMEs it performs -- exactly the single-pair atomicity that would
    leave a window here where `mutations_grain` is fresh and
    `mutation_parcelles` is stale, or vice-versa (T-57-18). The mechanism
    itself -- logged guard, drop a stale `_old`, RENAME pair, strip staging
    names from indexes/partitions/sequences -- is not reinvented: it is the
    same recipe `swap_tables` uses, applied to two pairs, inside ONE
    transaction the caller commits exactly once.
    """
    # Both candidates are created logged: published as loaded (plan 04.1-04).
    BasePipeline.assert_staging_logged(cur, "mutations_grain_staging")
    BasePipeline.assert_staging_logged(cur, "mutation_parcelles_staging")

    cur.execute("DROP TABLE IF EXISTS mutations_grain_old CASCADE")
    cur.execute("DROP TABLE IF EXISTS mutation_parcelles_old CASCADE")

    cur.execute("ALTER TABLE mutations_grain RENAME TO mutations_grain_old")
    cur.execute("ALTER TABLE mutations_grain_staging RENAME TO mutations_grain")
    cur.execute("ALTER TABLE mutation_parcelles RENAME TO mutation_parcelles_old")
    cur.execute("ALTER TABLE mutation_parcelles_staging RENAME TO mutation_parcelles")

    _liberer_noms_index_canoniques(cur)
    _renommer_index(cur)
    _renommer_partitions_grain(cur, departements)
    _renommer_sequences(cur)


def _liberer_noms_index_canoniques(cur: psycopg.Cursor) -> None:
    """Park the canonical index names still held by the just-renamed `_old` tables.

    `ALTER TABLE ... RENAME` does not cascade to indexes (the same
    PostgreSQL limitation `_renommer_partitions_grain` works around for
    partitions) -- right after the table RENAMEs above,
    `mutations_grain_old`/`mutation_parcelles_old` still carry the EXACT
    canonical index names migration 087 gave them (``idx_mutations_grain_id_
    mutation``, etc). Left alone, `_renommer_index` below collides on its
    first `ALTER INDEX ... RENAME TO <canonical>` ("relation already
    exists") -- found empirically on the very first real swap of this
    module, 57-07, since this pair of tables had never been swapped before
    and still carried migration 087's original index names. These `_old`
    tables are dropped wholesale by the NEXT run's own
    ``DROP TABLE ... CASCADE`` at the top of `_bascule_deux_paires`, so the
    parked name only needs to stay free until then -- its exact spelling
    does not matter.
    """
    for canonical, _ddl_suffix in _GRAIN_INDEXES + _PARCELLES_INDEXES:
        cur.execute(f"ALTER INDEX IF EXISTS {canonical} RENAME TO {canonical}_old")


def _renommer_index(cur: psycopg.Cursor) -> None:
    """Strip the `_staging` suffix from every index this module created."""
    for canonical, _ddl_suffix in _GRAIN_INDEXES + _PARCELLES_INDEXES:
        staged = BasePipeline.staging_index_name(canonical)
        cur.execute(f"ALTER INDEX IF EXISTS {staged} RENAME TO {canonical}")


def _renommer_partitions_grain(
    cur: psycopg.Cursor, departements: Sequence[str]
) -> None:
    """Rename `mutations_grain_staging_XX` partitions to `mutations_grain_XX`.

    `mutation_parcelles` is flat (not partitioned, D-09 -- migration 087
    docstring) -- nothing to rename there.
    """
    for dept in departements:
        safe = dept.lower()
        cur.execute(f"DROP TABLE IF EXISTS mutations_grain_{safe}")
        cur.execute(
            f"ALTER TABLE IF EXISTS mutations_grain_staging_{safe} "
            f"RENAME TO mutations_grain_{safe}"
        )
    cur.execute("DROP TABLE IF EXISTS mutations_grain_default")
    cur.execute(
        "ALTER TABLE IF EXISTS mutations_grain_staging_default "
        "RENAME TO mutations_grain_default"
    )


def _renommer_sequences(cur: psycopg.Cursor) -> None:
    """Point the production `id` sequence names back at the fresh tables.

    `ALTER TABLE ... RENAME` does not cascade to a DEFAULT's sequence, so
    both sequences still carry their staging names after the table RENAMEs
    above -- rename them here, same doctrine as `DvfPipeline.swap()` Phase C.
    """
    for prod, staging in (
        ("mutations_grain", "mutations_grain_staging"),
        ("mutation_parcelles", "mutation_parcelles_staging"),
    ):
        cur.execute(f"DROP SEQUENCE IF EXISTS {prod}_id_seq CASCADE")
        cur.execute(
            f"ALTER SEQUENCE IF EXISTS {staging}_id_seq RENAME TO {prod}_id_seq"
        )
