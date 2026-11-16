"""Raw SQL queries for DVF property transaction lookups.

Phase 57 (D-01/D-05/D-09/D-15): every function below reads the reconstructed
``mutations_grain`` (one row per ``id_mutation``, already qualified by
``app.etl_dvf.qualification.qualifie_mutation`` at ETL time) instead of the
raw per-line ``mutations`` table. ``prix_m2`` is no longer a per-line
division -- it is computed once here, at query time, from the already-decided
``classe_mutation``/``mono_local``/``est_vente``/``hors_stats_vefa`` columns
(``mutations_grain`` does not store ``prix_m2`` itself, D-15/reconstruction.py)
via the two shared SQL fragments below, which mirror
``app.etl_dvf.qualification._prix_m2`` decision table exactly -- the closed
taxonomy (``classe_mutation``, the mono-local/vendable flags) remains the
SINGLE point of truth (D-05); the SQL only re-derives the trivial arithmetic
consequence of a decision already made once, in Python, by the ETL.

Uses PostGIS ST_DWithin for spatial filtering and KNN (<-> operator)
for distance-ordered results with optional temporal and price filters.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl_dvf.qualification import TYPE_APPARTEMENT, TYPE_MAISON

# Public contract's `type_local` filter, mapped to the closed `classe_mutation`
# vocabulary (D-15, Threat T-57-37). Only the two mono-local residential
# classes have a direct `type_local` analog -- any other public value must
# resolve to a clause that returns nothing, never "tout remonter".
TYPE_LOCAL_VERS_CLASSE_MUTATION: dict[str, str] = {
    "Maison": "residentiel_maison",
    "Appartement": "residentiel_appartement",
}

# Classes retained by the building-scale listing (``dvf_appartements_par_parcelle``,
# D-17): the two residential classes that can contain an appartement. The
# mono-local one, and the whole-building sale -- which D-17 makes displayable
# precisely BECAUSE it is labelled as such ("vente d'ensemble -- prix global"),
# never as the price of one lot. Bound as a parameter, never interpolated.
CLASSES_LISTING_IMMEUBLE: tuple[str, ...] = (
    "residentiel_appartement",
    "ensemble_residentiel",
)

# Reverse projection for the response's `type_local`/`code_type_local`
# (D-15, "aucun champ retire"): only the two mono-local residential classes
# carry a single unambiguous label -- every other class (mixte, ensemble,
# commerciale, terrain_seul, hors_perimetre) mixes more than one kind of
# local or none at all, so the honest answer is NULL, never a guess. Built
# from the same TYPE_MAISON/TYPE_APPARTEMENT constants as the ETL
# qualification module -- no magic string duplicated.
_TYPE_LOCAL_SQL = """CASE mg.classe_mutation
        WHEN 'residentiel_maison' THEN 'Maison'
        WHEN 'residentiel_appartement' THEN 'Appartement'
        ELSE NULL
    END"""

_CODE_TYPE_LOCAL_SQL = f"""CASE mg.classe_mutation
        WHEN 'residentiel_maison' THEN '{TYPE_MAISON}'
        WHEN 'residentiel_appartement' THEN '{TYPE_APPARTEMENT}'
        ELSE NULL
    END"""

# Honest prix_m2 (D-15): NULL outside a mono-local residential sale/VEFA with
# a positive surface and price -- mirrors
# app.etl_dvf.qualification._prix_m2 exactly.
_PRIX_M2_SQL = """CASE
        WHEN mg.classe_mutation NOT IN (
            'residentiel_maison', 'residentiel_appartement'
        ) THEN NULL
        WHEN NOT (mg.est_vente OR mg.hors_stats_vefa) THEN NULL
        WHEN NOT mg.mono_local
             OR mg.surface_bati_totale IS NULL OR mg.surface_bati_totale <= 0
             OR mg.valeur_fonciere IS NULL OR mg.valeur_fonciere <= 0 THEN NULL
        ELSE ROUND(mg.valeur_fonciere / mg.surface_bati_totale)
    END"""

# The reason prix_m2 is NULL, drawn from the closed
# app.etl_dvf.qualification.RAISONS_PRIX_M2 vocabulary -- never a free
# string. Mutually exclusive with _PRIX_M2_SQL by construction (same WHEN
# ladder, same branches).
_PRIX_M2_RAISON_SQL = """CASE
        WHEN mg.classe_mutation = 'mixte' THEN 'vente_mixte'
        WHEN mg.classe_mutation = 'commerciale' THEN 'mutation_commerciale'
        WHEN mg.classe_mutation = 'terrain_seul' THEN 'terrain_seul'
        WHEN mg.classe_mutation NOT IN (
            'residentiel_maison', 'residentiel_appartement'
        ) THEN 'mutation_multi_locaux'
        WHEN NOT (mg.est_vente OR mg.hors_stats_vefa) THEN 'nature_hors_vente'
        WHEN NOT mg.mono_local
             OR mg.surface_bati_totale IS NULL OR mg.surface_bati_totale <= 0
             OR mg.valeur_fonciere IS NULL OR mg.valeur_fonciere <= 0
            THEN 'surface_absente'
        ELSE NULL
    END"""

# `nombre_lots` is PER-LOCAL metadata (D-02/Pitfall 3), never summed or
# maxed across a mutation's lines. Only meaningful when the mutation is
# mono-local (one residential/commercial local unambiguously owns it) --
# joined back to the line-level `mutations` detail on demand (D-08), never
# an aggregate mutations_grain itself would compute.
_NB_LOTS_SOURCE_SQL = """CASE WHEN mg.mono_local THEN (
        SELECT m2.nombre_lots FROM mutations m2
        WHERE m2.id_mutation = mg.id_mutation
          AND m2.code_departement = mg.code_departement
          AND m2.code_type_local IN ('1', '2')
        LIMIT 1
    ) END"""

# D-03 (Ianis, 2026-10-19): a mutation none of whose lines is geocoded is
# reconstructed with ``position`` NULL (phase 04.4, D-01) and serves the
# statistics only -- the commune ones, which read no position. It is absent
# from every read by address, parcel, position or radius. The single
# spelling of that exclusion, reused by the parcel and address readers; the
# radius readers already exclude it (``ST_DWithin`` on a NULL is not true).
POSITION_CONNUE_SQL = "mg.position IS NOT NULL"

# One deterministic representative parcelle for a spatially-found mutation
# that may touch more than one (D-09, nb_parcelles carries the true count).
# Never a guess at "the" parcel -- the lowest id_parcelle, same "first pick"
# discipline as the ETL reconstruction's array_agg(... ORDER BY id)[1].
_ID_PARCELLE_LATERAL_SQL = """LEFT JOIN LATERAL (
        SELECT mp.id_parcelle FROM mutation_parcelles mp
        WHERE mp.id_mutation = mg.id_mutation
        ORDER BY mp.id_parcelle
        LIMIT 1
    ) parc ON true"""

# Additive qualification columns (Task 1, D-15) shared by every function
# below that projects a full ResultatDvf row -- single point of truth for
# the column list so the 4 functions never drift from one another.
_COLONNES_QUALIFICATION_SQL = f"""
               {_TYPE_LOCAL_SQL} AS type_local,
               mg.surface_bati_totale AS surface_reelle_bati,
               mg.nombre_pieces_totales AS nombre_pieces_principales,
               mg.surface_terrain_totale AS surface_terrain,
               {_PRIX_M2_SQL} AS prix_m2,
               mg.adresse_numero, mg.adresse_nom_voie, mg.code_postal,
               mg.nom_commune,
               {_CODE_TYPE_LOCAL_SQL} AS code_type_local,
               NULL::text AS code_nature_culture,
               NULL::text AS nature_culture,
               NULL::text AS code_nature_culture_speciale,
               NULL::text AS nature_culture_speciale,
               ST_X(mg.position) AS longitude,
               ST_Y(mg.position) AS latitude,
               mg.classe_mutation,
               {_PRIX_M2_RAISON_SQL} AS prix_m2_raison,
               mg.mono_local AS mutation_mono_local,
               mg.nb_locaux_maison, mg.nb_locaux_appartement,
               mg.nb_locaux_commercial,
               {_NB_LOTS_SOURCE_SQL} AS nb_lots_source,
               mg.nb_parcelles, mg.hors_stats_vefa, mg.surface_bati_totale"""


async def dvf_par_point(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 500.0,
    annee_min: int | None = None,
    annee_max: int | None = None,
    prix_min: float | None = None,
    prix_max: float | None = None,
    type_local: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Find DVF transactions (mutations) near a GPS point with optional filters.

    Spatial filter uses ST_DWithin on the geography GIST index. Results
    are ordered by geodesic KNN distance on that same index: migration 107
    dropped the geometry index a planar sort needs (audit C10, 04.7-01).
    All filter values use bind parameters for SQL injection safety.

    Reads ``mutations_grain`` (Phase 57, D-01): each row is a complete,
    already-qualified mutation, not a line. ``surface_reelle_bati`` and
    ``nombre_pieces_principales`` are kept by name but now carry the
    MUTATION's total (``surface_bati_totale``/``nombre_pieces_totales``),
    not a single line's figure -- documented semantics change on a
    preserved field (D-15). ``id_parcelle`` is one representative parcel
    when the mutation touches several (see ``nb_parcelles``).
    """
    # Build WHERE clause dynamically -- only structure changes, values via params
    where_clauses = [
        "ST_DWithin("
        "    mg.position::geography,"
        "    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,"
        "    :rayon_m"
        ")"
    ]
    params: dict = {
        "lat": lat,
        "lon": lon,
        "rayon_m": rayon_m,
        "limit": limit,
        "offset": offset,
    }

    if annee_min is not None:
        where_clauses.append("EXTRACT(YEAR FROM mg.date_mutation) >= :annee_min")
        params["annee_min"] = annee_min

    if annee_max is not None:
        where_clauses.append("EXTRACT(YEAR FROM mg.date_mutation) <= :annee_max")
        params["annee_max"] = annee_max

    if prix_min is not None:
        where_clauses.append("mg.valeur_fonciere >= :prix_min")
        params["prix_min"] = prix_min

    if prix_max is not None:
        where_clauses.append("mg.valeur_fonciere <= :prix_max")
        params["prix_max"] = prix_max

    if type_local is not None:
        classe = TYPE_LOCAL_VERS_CLASSE_MUTATION.get(type_local)
        if classe is not None:
            where_clauses.append("mg.classe_mutation = :classe_mutation")
            params["classe_mutation"] = classe
        else:
            # Unknown public value: return nothing, never everything.
            where_clauses.append("FALSE")

    where_sql = " AND ".join(where_clauses)

    sql = text(f"""
        SELECT mg.id_mutation, mg.date_mutation, mg.nature_mutation,
               mg.valeur_fonciere,{_COLONNES_QUALIFICATION_SQL},
               parc.id_parcelle
        FROM mutations_grain mg
        {_ID_PARCELLE_LATERAL_SQL}
        WHERE {where_sql}
        ORDER BY mg.position::geography
            <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


async def dvf_par_adresse(
    session: AsyncSession,
    lat: float,
    lon: float,
    rayon_m: float = 50.0,
    annee_min: int | None = None,
    annee_max: int | None = None,
    prix_min: float | None = None,
    prix_max: float | None = None,
    type_local: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """DVF sales positioned within *rayon_m* of a point.

    A proximity search around one geocoded point (C52, B1): a sale of a
    neighbouring building inside the radius may be listed, a sale of the
    address positioned further away may be missing. Never the history of an
    address nor of a building. Results are ordered by date descending.

    Sales and VEFA (C55 fix 2, C56, D-12): ``est_vente OR hors_stats_vefa``,
    the predicate of the similar sales (``_FILTRE_COMMUN`` of
    :mod:`app.adresse.ventes`). The sales declared at an address list the
    VEFA on request only (``vefa=incluses``, plan 04.1-15). Listed
    natures (:mod:`app.etl_dvf.qualification`): exactly « Vente »
    (``est_vente``) and « Vente en l'état futur d'achèvement » (a sale
    excluded from statistics only, flagged ``hors_stats_vefa``). Every other
    nature is not listed: « Vente terrain à bâtir », « Echange »,
    « Adjudication », « Expropriation ».

    One row per mutation (plan 04.1-15, D-128): ``id_mutation`` is not
    unique in ``mutations_grain``; ``DISTINCT ON (mg.id_mutation)`` keeps
    its row of lowest key ``id`` (one sequence for every partition), the
    rule of the address page lists (:mod:`app.adresse.ventes`). Pages are
    cut by offset: the order ends with the mutation key, now unique, so ties
    of date and price never repeat nor skip a sale between pages (C55, fix 3).

    Reads ``mutations_grain`` (Phase 57, D-01) -- same grain/semantics notes
    as :func:`dvf_par_point`.
    """
    where_clauses = [
        "ST_DWithin("
        "    mg.position::geography,"
        "    ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,"
        "    :rayon_m"
        ")",
        "(mg.est_vente OR mg.hors_stats_vefa)",
    ]
    params: dict = {
        "lat": lat,
        "lon": lon,
        "rayon_m": rayon_m,
        "limit": limit,
        "offset": offset,
    }

    if annee_min is not None:
        where_clauses.append("EXTRACT(YEAR FROM mg.date_mutation) >= :annee_min")
        params["annee_min"] = annee_min

    if annee_max is not None:
        where_clauses.append("EXTRACT(YEAR FROM mg.date_mutation) <= :annee_max")
        params["annee_max"] = annee_max

    if prix_min is not None:
        where_clauses.append("mg.valeur_fonciere >= :prix_min")
        params["prix_min"] = prix_min

    if prix_max is not None:
        where_clauses.append("mg.valeur_fonciere <= :prix_max")
        params["prix_max"] = prix_max

    if type_local is not None:
        classe = TYPE_LOCAL_VERS_CLASSE_MUTATION.get(type_local)
        if classe is not None:
            where_clauses.append("mg.classe_mutation = :classe_mutation")
            params["classe_mutation"] = classe
        else:
            where_clauses.append("FALSE")

    where_sql = " AND ".join(where_clauses)

    sql = text(f"""
        WITH une_par_mutation AS (
            SELECT DISTINCT ON (mg.id_mutation)
                   mg.id_mutation, mg.date_mutation, mg.nature_mutation,
                   mg.valeur_fonciere,{_COLONNES_QUALIFICATION_SQL},
                   parc.id_parcelle
            FROM mutations_grain mg
            {_ID_PARCELLE_LATERAL_SQL}
            WHERE {where_sql}
            ORDER BY mg.id_mutation, mg.id
        )
        SELECT * FROM une_par_mutation
        ORDER BY date_mutation DESC, valeur_fonciere DESC, id_mutation
        LIMIT :limit OFFSET :offset
    """)

    result = await session.execute(sql, params)
    return [dict(row._mapping) for row in result]


async def dvf_par_id_parcelle(
    session: AsyncSession,
    id_parcelle: str,
    limit: int = 5,
) -> list[dict]:
    """Find DVF transactions (mutations) of the CANONICAL cadastre parcel.

    (Phase 45, D-02; Phase 57, D-01/D-09.) Direct filter on the authoritative
    14-char ``id_parcelle`` via the ``mutation_parcelles`` link table -- no
    point, no centroid, no radius. This is the only DVF path used once the
    resolver has established the canonical identity, so a concave parcel
    whose centroid falls inside a neighbour can never surface the
    neighbour's sales. A mutation spanning several parcels (D-09) appears on
    the Fiche of EACH parcel it touches -- this is the expected behaviour,
    not a duplicate to deduplicate.

    ``code_departement`` (the partition key of ``mutations_grain``) is
    derived from ``id_parcelle`` so the scan prunes to a single partition
    (DOM 97x → 3 chars, else 2). Ordered by date DESC so ``[0]`` is the
    latest sale.
    """
    sql = text(f"""
        SELECT mg.id_mutation, mg.date_mutation, mg.nature_mutation,
               mg.valeur_fonciere,{_COLONNES_QUALIFICATION_SQL},
               mp.id_parcelle
        FROM mutations_grain mg
        JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation
        WHERE mp.id_parcelle = :id_parcelle
          AND mg.code_departement = CASE
              WHEN LEFT(:id_parcelle, 2) = '97' THEN LEFT(:id_parcelle, 3)
              ELSE LEFT(:id_parcelle, 2)
          END
          AND {POSITION_CONNUE_SQL}
        ORDER BY mg.date_mutation DESC, mg.valeur_fonciere DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, {"id_parcelle": id_parcelle, "limit": limit})
    return [dict(row._mapping) for row in result]


# Mutations of ONE parcel (04-03, reused by the sales list of 04-05): the
# link table names the exact 14-char parcel, ``mutations_grain`` carries one
# row per complete mutation, the partition is pruned by the departement of
# the parcel. Whole imported DVF period, every nature: the count and the list
# read the same population, without the mutations of unknown position (D-03).
# No LIMIT here: each reader bounds itself.
_MUTATIONS_DE_LA_PARCELLE_SQL = f"""
        FROM mutations_grain mg
        JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation
        WHERE mp.id_parcelle = :id_parcelle
          AND mg.code_departement = CASE
              WHEN LEFT(:id_parcelle, 2) = '97' THEN LEFT(:id_parcelle, 3)
              ELSE LEFT(:id_parcelle, 2)
          END
          AND {POSITION_CONNUE_SQL}
"""


async def dvf_compte_par_id_parcelle(
    session: AsyncSession,
    id_parcelle: str,
) -> dict:
    """Number of distinct mutations of the parcel and the date of the latest.

    A mutation spanning several parcels counts once on each parcel it
    touches, never twice on one. ``derniere`` is None without mutation.
    """
    sql = text(f"""
        SELECT COUNT(DISTINCT mg.id_mutation) AS nombre,
               MAX(mg.date_mutation) AS derniere
        {_MUTATIONS_DE_LA_PARCELLE_SQL}
    """)
    result = await session.execute(sql, {"id_parcelle": id_parcelle})
    row = result.mappings().one()
    return {"nombre": int(row["nombre"]), "derniere": row["derniere"]}


async def dvf_par_parcelle(
    session: AsyncSession,
    lat: float,
    lon: float,
    limit: int = 5,
) -> list[dict]:
    """Find DVF transactions (mutations) attached to the exact cadastre parcel.

    Audited legacy wrapper (Phase 45, D-03; Phase 57, D-01/D-09): kept for
    callers without a resolved identity. Any path that already holds the
    canonical ``id_parcelle`` must use :func:`dvf_par_id_parcelle` instead --
    this query still performs its own point-in-polygon parcel selection.

    The « sur ce bien » fiche block must show the sales of THIS parcel, not a
    proximity radius (which mislabeled neighbours' sales as the bien's last
    sale). Resolves the parcel containing the point (GIST on parcelles), then
    filters mutations via the ``mutation_parcelles`` link table (D-09) by the
    authoritative 14-char ``id_parcelle`` — the same key Etalab uses to
    attach a DVF row to a parcel.

    ``code_departement`` (the partition key of ``mutations_grain``) is
    derived from ``id_parcelle`` so the join prunes to a single partition.
    The DOM branch (97x → 3 chars) mirrors the ETL's
    ``_derive_code_departement``; metro/Corse use the first 2 chars.

    Ordered by date DESC so ``[0]`` is the latest sale. Returns an empty list
    when the point is on no parcel, or the parcel has no recorded sale.
    """
    sql = text(f"""
        WITH parc AS (
            SELECT id_parcelle
            FROM parcelles
            WHERE ST_Intersects(
                geometrie,
                ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
            )
            LIMIT 1
        )
        SELECT mg.id_mutation, mg.date_mutation, mg.nature_mutation,
               mg.valeur_fonciere,{_COLONNES_QUALIFICATION_SQL},
               mp.id_parcelle
        FROM mutations_grain mg
        JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation
        JOIN parc ON mp.id_parcelle = parc.id_parcelle
            AND mg.code_departement = CASE
                WHEN LEFT(parc.id_parcelle, 2) = '97' THEN LEFT(parc.id_parcelle, 3)
                ELSE LEFT(parc.id_parcelle, 2)
            END
        WHERE {POSITION_CONNUE_SQL}
        ORDER BY mg.date_mutation DESC, mg.valeur_fonciere DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, {"lat": lat, "lon": lon, "limit": limit})
    return [dict(row._mapping) for row in result]


async def dvf_mutation_unique_isolee(
    session: AsyncSession,
    departements: list[str],
    fenetre_mois: int = 24,
    limit: int = 200,
) -> list[dict]:
    """Detect parcels with a single, recent, isolated mutation (Phase 47, D-03).

    Feeds the minimization/re-identification review (FICHE-02) and any
    conditional hardening it decides on. Additive and SEPARATE from
    ``dvf_par_id_parcelle`` on purpose (RESEARCH Pitfall 4): the shared query
    used by the gated (app) fiche and general API must never be touched by
    this detection -- any post-filter this feeds must live at the public
    route boundary only, never inside the shared query.

    Phase 57 (D-01): reads ``mutations_grain`` joined to
    ``mutation_parcelles`` so ``COUNT(*)`` counts MUTATIONS touching a
    parcel, not raw DVF lines -- the pre-Phase-57 version counted lines,
    inflating the count on any multi-line mutation.
    """
    sql = text("""
        SELECT mp.id_parcelle, COUNT(*) AS nb_mutations, MAX(date_mutation) AS derniere
        FROM mutations_grain mg
        JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation
        WHERE code_departement = ANY(:departements)
        GROUP BY mp.id_parcelle
        HAVING COUNT(*) = 1
           AND MAX(date_mutation) >= NOW() - (:fenetre_mois || ' months')::interval
        LIMIT :limit
    """)

    result = await session.execute(
        sql,
        {"departements": departements, "fenetre_mois": fenetre_mois, "limit": limit},
    )
    return [dict(row._mapping) for row in result]


async def dvf_appartements_par_parcelle(
    session: AsyncSession,
    id_parcelle: str,
    surface_min: int = 10,
    limit: int = 20,
) -> list[dict]:
    """List the appartement-bearing MUTATIONS of one parcel (Phase 48; D-17).

    Additive and SEPARATE from ``dvf_par_id_parcelle`` on purpose (same
    discipline as ``dvf_mutation_unique_isolee``): the shared query used by
    the gated (app) fiche and general API must never be touched by this
    building-scale listing.

    Reads ``mutations_grain`` joined to ``mutation_parcelles`` (Phase 57,
    D-01/D-09): one row is one complete, already-qualified MUTATION, never a
    DVF line. ``code_departement`` is derived from ``id_parcelle`` (Pattern
    B, same as ``dvf_par_id_parcelle``) so the scan prunes to one partition.

    Selection is now a ``classe_mutation`` filter instead of
    ``type_local = 'Appartement'``: the residential classes that contain
    appartement are ``residentiel_appartement`` (mono-local) and
    ``ensemble_residentiel`` (several residential locals sold at once) --
    both bound as parameters, so an unknown class can never widen the
    result. The ``surface_min`` floor (default 10 m2, meant to discard
    caves/parkings/residual lots, D-09) now applies to the mutation's
    ``surface_bati_totale``, which is more faithful: dependencies carry no
    surface of their own and therefore no longer drag the threshold down.

    PRICE, revision D-17. The Phase 48 interdiction (D-16, « the immeuble
    sales block carries no price on its cards ») targeted a PER-LINE price:
    reading ``mutations`` line by line, each row showed the mutation's TOTAL
    ``valeur_fonciere`` beside a SINGLE local's surface -- a figure that was
    simply false on any multi-local sale. That cause is gone: the row below
    is the whole mutation, so its amount is the amount actually paid, and it
    is projected ONLY together with ``classe_mutation``, which says what was
    sold ("vente d'ensemble -- prix global", mono-local, ...). A price at
    the m2 (``prix_m2``) still exists only where it is true -- a mono-local
    residential sale -- and carries its ``prix_m2_raison`` otherwise (D-15).

    A compatible mutation observed on this parcel is NEVER the certain price
    of a given dwelling nor the proof that a given dwelling was sold (D-37):
    this listing is the parcel's transaction history, and the caller labels
    each row by its class. No DPE<->sale correspondence is asserted here --
    fuzzy matching (L3) is out of scope for this function.
    """
    sql = text(f"""
        SELECT mg.date_mutation,
               {_TYPE_LOCAL_SQL} AS type_local,
               mg.surface_bati_totale AS surface_reelle_bati,
               mg.nombre_pieces_totales AS nombre_pieces_principales,
               mg.valeur_fonciere,
               mg.classe_mutation,
               mg.mono_local,
               mg.hors_stats_vefa,
               {_PRIX_M2_SQL} AS prix_m2,
               {_PRIX_M2_RAISON_SQL} AS prix_m2_raison,
               (mg.nb_locaux_maison + mg.nb_locaux_appartement)
                   AS nb_locaux_residentiel,
               mg.nb_locaux_commercial
        FROM mutations_grain mg
        JOIN mutation_parcelles mp ON mp.id_mutation = mg.id_mutation
        WHERE mp.id_parcelle = :id_parcelle
          AND mg.classe_mutation = ANY(:classes_mutation)
          AND mg.surface_bati_totale >= :surface_min
          AND mg.code_departement = CASE
              WHEN LEFT(:id_parcelle, 2) = '97' THEN LEFT(:id_parcelle, 3)
              ELSE LEFT(:id_parcelle, 2)
          END
          AND {POSITION_CONNUE_SQL}
        ORDER BY mg.date_mutation DESC
        LIMIT :limit
    """)

    result = await session.execute(
        sql,
        {
            "id_parcelle": id_parcelle,
            "classes_mutation": list(CLASSES_LISTING_IMMEUBLE),
            "surface_min": surface_min,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in result]
