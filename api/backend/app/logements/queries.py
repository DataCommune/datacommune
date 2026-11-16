"""Raw SQL queries for the logements module (Phase 48, D-10).

Bounded, parameterized fetches -- no dedup/merge logic here, that lives in
``app.logements.service`` (D-10: SQL selects, Python decides).

Two DPE access paths, in this order (RESEARCH-L1-RATTACHEMENT-DPE-2026-07-31,
step 2):

1. ``dpe_appartements_par_ban`` -- EXACT join on the BAN interoperability key
   (``diagnostics_dpe.identifiant_ban = adresses.ban_id``, format
   ``INSEE_FANTOIR_NUMERO``). This is the primary path: ADEME already did the
   address matching upstream and fills the key on 99,96 % of dept-44 rows.
2. ``dpe_appartements_par_parcelle`` -- bounded SPATIAL fallback, for the rows
   the key cannot carry (``identifiant_ban IS NULL``) or when the caller has no
   BAN key at all.

A third path existed between 2026-08-05 and 2026-08-06: BUILDING PROOF (Lot C2),
which attached an orphan-key DPE row when its own ``id_rnb`` named an RNB
building carrying the resolved key. It was REMOVED after measurement, and the
reason is worth keeping: it was not merely useless, it was harmful. Measured on
the 115 orphan-branch fiches of the sweep corpus against production data, it
fired on 1 fiche, yielded 0 card there -- the street-number filter downstream
excluded everything it found, since rows fetched by BUILDING carry the building's
OTHER street numbers -- and, because ``_charge_dpe`` is an ``if not rows``
cascade whose filtering happens later in the handler, its non-empty result
SHORT-CIRCUITED the full spatial fallback: that fiche served 0 card where the
fallback served 3. Giving it its own street reference (the carrier's keys) would
have yielded 15 cards on that same fiche, all from a 16-key carrier spanning 4
streets -- precisely what the width ceiling of
:data:`~app.logements.service.PLAFOND_LARGEUR_PORTEUR` deliberately blocks. Do
not reintroduce it without a measured benefit expressed in ADDRESSES reachable
through ``/v1/resolve``.

:func:`cles_ban_batiment_rnb` feeds path 1: it widens the single resolved key
to every OTHER BAN key of the same RNB building (corner building, R4-02). It
lives here and not in a service module because, since Lot C1, it is nothing
but a parameterized read of the local ``rnb_liens`` table; the one rule it
applies on top -- the carrier width ceiling, recalibrated 2026-08-06 -- is a
pure function it imports from ``app.logements.service``
(:func:`~app.logements.service.applique_plafond_largeur`), so the split still
holds: SQL selects, Python decides.

The fallback used to be a strict ``ST_Intersects`` on the parcel geometry, and
that was the L1 bug: an ADEME DPE position IS the BAN entrance point, which
frequently sits on the roadway, strictly OUTSIDE every parcel (2 rue des Essais,
Nantes: 0,0 m to the abutting parcel, 9,6 m to the runner-up) -- the same
mechanism the resolve snap fixed for ``parcelle_par_point`` on 2026-07-31.
It now tolerates :data:`SECOURS_RAYON_M` metres, but ONLY under the resolve
snap's non-ambiguity rule (:data:`app.resolve.snap.SNAP_MARGIN_M`): a point
outside the parcel is attached only when this parcel leads every other one by
that margin. A contained point stays certain and keeps its Phase 45
guarantee unconditionally. The 500 m radius of ``dpe_par_point``
(``app.dpe.queries``) remains excluded -- it would surface neighbours' DPE
rows as this building's logements ("invented list" regression).

Projects its own column list rather than reusing the shared DPE column
constant used by ``app.dpe.queries``/``app.zone.queries`` (locked by
``tests/test_dpe_columns_shared.py``): the logement columns
(``numero_etage_appartement``, ``complement_adresse_logement``, ...) are not
part of that shared contract.
"""

import logging
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.imported_tables import IMPORTED_TABLES, publication_actee
from app.logements.service import applique_plafond_largeur
from app.resolve.snap import SNAP_MARGIN_M

logger = logging.getLogger(__name__)

# Search radius (metres) of the SPATIAL FALLBACK path. Wider than the resolve
# snap's 10 m (a DPE point is matched against the parcel POLYGON, not against
# the parcel it snapped to) and still far below any neighbouring-building
# distance in a dense block.
SECOURS_RAYON_M = 12.0

_DPE_COLS: tuple[str, ...] = (
    "numero_dpe",
    "date_etablissement_dpe",
    "date_fin_validite_dpe",
    "numero_dpe_remplace",
    "type_batiment",
    "surface_habitable",
    "etiquette_dpe",
    "numero_etage_appartement",
    "complement_adresse_logement",
    "identifiant_ban",
)


def _cols(alias: str) -> str:
    """Project the DPE column list under *alias*, same order for every path."""
    return ",\n            ".join(f"{alias}.{col}" for col in _DPE_COLS)


_DPE_SELECT_COLS = _cols("d")

# Partition key derived from a 14-char cadastral reference or from a BAN
# interoperability key -- both start with the INSEE code (Pattern B, same
# derivation as ``dvf_par_id_parcelle``).
_DEPT_DEPUIS = """CASE
                  WHEN LEFT(:{param}, 2) = '97' THEN LEFT(:{param}, 3)
                  ELSE LEFT(:{param}, 2)
              END"""

_COPROPRIETE_SELECT_COLS = """
    cp.numero_immatriculation,
    cp.nom_copropriete,
    cp.nb_lots_total,
    cp.nb_lots_habitation_strict,
    cp.periode_construction
"""
# 04.7-08: the manager and its mandate, read by the address report only; the
# name of a volunteer manager (a person) never leaves the report reader.
_COPROPRIETE_GESTION_COLS = """,
    cp.type_syndic,
    cp.nom_syndic,
    cp.mandat_en_cours,
    cp.date_fin_dernier_mandat
"""


def _dept_depuis_cle(cle: str) -> str:
    """Partition key from a BAN interoperability key (Pattern B, in Python).

    Same derivation as the SQL :data:`_DEPT_DEPUIS`, applied per key because
    the widened BAN path binds a LIST of keys (R4-02) and each key carries
    its own INSEE prefix.
    """
    return cle[:3] if cle.startswith("97") else cle[:2]


# The "porteurs" of a resolved BAN key: the buildings standing on the parcel
# (``batiments_parcelle``) that CARRY that key. Introduced by Lot C1 for the
# Rule 2 widening, which is its only consumer since the building-proof path was
# removed (2026-08-06, see the module docstring).
#
# ``@>`` is mandatory on the first step: it is the containment operator
# ``idx_rnb_liens_parcelles`` (GIN) answers -- ``= ANY(parcelles)`` would
# force a sequential scan of the 1,5 M-row table on the hot path of a public
# endpoint. No index is added for the widening: the three access paths it uses
# already exist (GIN on ``parcelles``, UNIQUE ``(id_rnb, cle_ban)`` for
# building -> keys, btree on ``cle_ban`` for key -> carriers).
_CTE_PORTEURS_BATIMENT = """
WITH batiments_parcelle AS (
    SELECT DISTINCT id_rnb
    FROM rnb_liens
    WHERE parcelles @> ARRAY[:id_parcelle]::text[]
),
porteurs AS (
    SELECT DISTINCT l.id_rnb
    FROM rnb_liens l
    JOIN batiments_parcelle b ON b.id_rnb = l.id_rnb
    WHERE l.cle_ban = :ban_id
)"""

# Regle 2 (elargissement batiment), Lot C1: every OTHER BAN key those
# porteurs carry, plus the width of the WIDEST porteur -- the distinct keys it
# carries in the whole table, deliberately NOT parcel-scoped (the width is an
# intrinsic property of the building, see ``applique_plafond_largeur``). Still
# one round trip: the scalar rides duplicated on every row rather than
# costing a second query on the hot path; each porteur's keys are read
# through the UNIQUE ``(id_rnb, cle_ban)`` index, 1-3 porteurs per fiche.
_SQL_CLES_BATIMENT_RNB = f"""{_CTE_PORTEURS_BATIMENT},
largeurs AS (
    SELECT count(DISTINCT l.cle_ban) AS largeur
    FROM rnb_liens l
    JOIN porteurs p ON p.id_rnb = l.id_rnb
    GROUP BY l.id_rnb
)
SELECT DISTINCT l.cle_ban, (SELECT max(largeur) FROM largeurs) AS largeur_porteur
FROM rnb_liens l
JOIN porteurs p ON p.id_rnb = l.id_rnb
WHERE l.cle_ban <> :ban_id
"""


async def cles_ban_batiment_rnb(
    session: AsyncSession,
    id_parcelle: str,
    ban_id: str,
) -> tuple[str, ...]:
    """The OTHER BAN keys of the building(s) carrying *ban_id* on *id_parcelle*.

    Rule 2 of the DPE attachment (R4-02, Plan A'). One building can carry
    SEVERAL BAN interoperability keys -- the corner building with two
    entrances on two streets (``2 rue des Essais`` + ``12 rue Felibien``) --
    and its DPE set is split across them, so the exact-key path
    (:func:`dpe_appartements_par_ban`) under-collects when it only knows the
    single resolved key. These keys widen its ``IN (...)``.

    The unit of widening is the BUILDING, never the parcel: a parcel routinely
    carries several buildings at different street numbers. Merging their keys
    published the neighbour's flats -- on 44109000HN0129 the fiche of n°1 (3
    appartement DPE) showed 9 cards by pulling in the 8 DPE of n°3 (regression
    measured and fixed 2026-08-04). The ``batiments_parcelle``/``porteurs``
    CTEs are what keeps that partition.

    MULTI-CARRIER (decision 2026-08-05, gate ``GATE-EQUIVALENCE-RNB``): when
    2-3 RNB buildings carry the SAME resolved key -- a multi-wing residence
    sharing one entrance -- the UNION of all their keys is returned. The live
    RNB call this replaced returned the keys of the FIRST carrier in the
    upstream's arbitrary response order: order-dependent and under-collecting.
    The union is deterministic and a superset, so no card is ever lost.

    NOTHING DOWNSTREAM CONTAINS THE OVER-COLLECTION, and that is not an
    oversight to fix elsewhere -- it is a property of the design, measured
    2026-08-05. :func:`app.logements.service.filtre_par_numero_voie` derives
    its reference numbers from ``cles_ban``, i.e. from the widened set itself,
    so every widened row matches by construction; the filter only bites on
    rows the spatial fallback brought in. On the 17 multi-carrier fiches of
    the corpus, 17/17 bring in a DIFFERENT street number -- widening across
    street numbers IS what this rule does (that is the corner building).

    WIDTH CEILING (recalibrated 2026-08-06, second guard alongside the
    building partition of the CTEs above): when the WIDEST carrier of the
    resolved key carries more than
    :data:`~app.logements.service.PLAFOND_LARGEUR_PORTEUR` distinct BAN keys
    in the whole table, NOTHING is widened. Counted per CARRIER, never on the
    union -- the union of narrow carriers is the multi-wing residence the
    union decision above protects, while one building carrying 16 keys over
    4 streets is an RNB aggregation artefact. The 2026-08-05 ceiling measured
    the widened SET instead (2 streets / 4 keys) and withheld 29 legitimate
    diagnostics from a real 3-street corner building (proven false positive,
    internal note INSTRUMENTATION-PLAFOND-GEOMETRIQUE-2026-08-06). Applied
    here rather than at the caller so no future caller can obtain a raw,
    uncapped widening; the threshold and its calibration live with
    :func:`~app.logements.service.applique_plafond_largeur`.

    ``ban_id`` is NEVER in the result (``cle_ban <> :ban_id``). That is a
    contract, not an optimisation: :func:`app.logements.service.
    marque_rattachement_ban` tells ``exact`` from ``elargi_batiment`` by
    exactly this exclusion.

    Degraded behaviour: no building on the parcel, no carrier, an empty
    ``rnb_liens``, an ``rnb_liens`` that is not the table of its last acted
    publication, or a building above the width ceiling -> ``()``, i.e. the
    strict single-key behaviour. Silently for the caller, but the ceiling
    case is traced (counts only, never a key: a BAN key is personal data
    here).
    There is no fail-open wrapper here (unlike the live RNB call replaced in
    Lot C1): ``rnb_liens`` is OUR table, so a real SQL error is a bug of ours
    and propagates like every other query of this module.

    Sorted in Python rather than by an ``ORDER BY``: the caller (a bound
    ``IN (...)``) is order-insensitive -- sorting only makes the tuple, and
    the logs quoting it, reproducible. The key-selecting clauses are the ones
    the equivalence gate validated (2026-08-05); the ``largeurs`` CTE added
    2026-08-06 rides a scalar alongside them and filters nothing.
    """
    if not id_parcelle or not ban_id:
        return ()
    # A swapped rnb_liens whose publication was not acted is not read: the
    # strict single-key behaviour, as for an empty table (04.7-19 review, P2).
    if not await publication_actee(session, [IMPORTED_TABLES["rnb"]]):
        logger.warning("logements: rnb_liens publication not acted, no widening")
        return ()

    result = await session.execute(
        text(_SQL_CLES_BATIMENT_RNB),
        {"id_parcelle": id_parcelle, "ban_id": ban_id},
    )
    rows = list(result)
    elargies = tuple(sorted({row[0] for row in rows if row[0]}))
    largeur_porteur = max((row[1] for row in rows if row[1] is not None), default=0)

    plafond = applique_plafond_largeur(elargies, largeur_porteur)
    if plafond.depasse:
        # PRIV-01 (app.resolve.telemetry): parcel identifiers never reach a
        # log record. The width that decided and the count it dropped are the
        # signal; the parcel is not. Gated by tests/test_resolve_privacy.py.
        logger.info(
            "logements: elargissement batiment annule (plafond de largeur) "
            "-- porteur le plus large %d cles, %d cles ecartees",
            plafond.largeur_porteur,
            len(elargies),
        )
    return plafond.cles


async def dpe_appartements_par_ban(
    session: AsyncSession,
    ban_ids: Sequence[str],
    limit: int = 120,
) -> list[dict]:
    """Find appartement DPE attached to EXACT BAN address keys (L1, step 2).

    ``ban_ids`` is the UNION of the resolved address key and the keys of the
    same RNB building (Plan A', R4-02): a corner building with two entrances
    on two streets carries one DPE set split across two BAN keys, and the
    single-key equality under-collected it. With one key the query keeps the
    exact pre-R4-02 semantics.

    Primary attachment path. ``diagnostics_dpe.identifiant_ban`` is the BAN
    interoperability key (``INSEE_FANTOIR_NUMERO``, e.g. ``44109_zz01_00002``)
    produced by ADEME itself, byte-identical to ``adresses.ban_id``: an
    equality join, no geometry, no text matching, no fuzzy score
    (RESEARCH-L1 §1.4 Plan A). Measured 99,96 % filled on dept 44.

    Filters ``type_batiment = 'appartement'``: a collective ('immeuble') DPE
    is never a logement card (Pitfall 5). ``code_departement`` is derived
    per key from its leading INSEE code (:func:`_dept_depuis_cle`) so the
    scan still prunes to the concerned partitions only.
    Ordered by date DESC; ``limit`` is a hard bound (one address in Vendee
    carries 324 appartement DPE rows -- Pitfall 3).

    Deliberately NOT filtered on ``statut_geocodage``: that ADEME field is
    incoherent (6 of the 10 Essais rows are flagged "non geocodee" while
    carrying a valid key and the right coordinates) and filtering on it would
    drop ~24 % of the corpus for nothing (RESEARCH-L1 §1.3).
    """
    cles = [c for c in dict.fromkeys(ban_ids) if c]
    if not cles:
        return []
    depts = sorted({_dept_depuis_cle(c) for c in cles})
    sql = text(f"""
        SELECT {_DPE_SELECT_COLS}
        FROM diagnostics_dpe d
        WHERE d.identifiant_ban IN :ban_ids
          AND d.code_departement IN :depts
          AND d.type_batiment = 'appartement'
        ORDER BY d.date_etablissement_dpe DESC
        LIMIT :limit
    """).bindparams(
        bindparam("ban_ids", expanding=True),
        bindparam("depts", expanding=True),
    )

    result = await session.execute(
        sql, {"ban_ids": cles, "depts": depts, "limit": limit}
    )
    return [dict(row._mapping) for row in result]


async def dpe_appartements_par_parcelle(
    session: AsyncSession,
    id_parcelle: str,
    limit: int = 120,
    seulement_sans_cle: bool = False,
) -> list[dict]:
    """Attach appartement DPE to a parcel spatially -- FALLBACK path (L1).

    Secondary to :func:`dpe_appartements_par_ban`. Two guarantees, in this
    order:

    - a DPE position CONTAINED by the parcel is attached unconditionally
      (``distance_m = 0``): that is the pre-existing ``ST_Intersects``
      certainty, kept intact -- a neighbouring parcel 1 m away never
      disqualifies a point that is inside this one;
    - a position OUTSIDE the parcel is attached only within
      :data:`SECOURS_RAYON_M` metres AND when this parcel leads every other
      parcel by :data:`app.resolve.snap.SNAP_MARGIN_M` -- the exact
      non-ambiguity rule of the resolve snap (``select_snapped_candidates``),
      reused rather than re-invented. A tie attaches nothing: never a silent
      pick among close neighbours (Phase 45 doctrine).

    ``seulement_sans_cle`` restricts the scan to ``identifiant_ban IS NULL``
    rows: that is how the caller COMPLETES the exact BAN path without
    re-fetching (and re-ranking spatially) rows the key already attached.

    Two-stage spatial filter copied from ``cadastre.parcelles_proches``: a
    predicate in degrees (padded by ``1/cos(lat)``, over-selects only), then
    the exact metre test on ``geography``. Since migration 107 no planar GiST
    index covers the 4326 GEOMETRY column of ``diagnostics_dpe``, so the
    degree predicate is no longer index-assisted. The function has no caller
    in ``app/`` (inventory of plan 04.1-23); if it is ever wired, recreate
    that index with it (registry ``04.1-INDEX-EXPLAIN.md``, FRAGILE).
    """
    dept = _DEPT_DEPUIS.format(param="id_parcelle")
    filtre_cle = "AND d.identifiant_ban IS NULL" if seulement_sans_cle else ""
    sql = text(f"""
        WITH parc AS (
            SELECT geometrie FROM parcelles
            WHERE id_parcelle = :id_parcelle
              AND code_departement = {dept}
            LIMIT 1
        ),
        candidats AS (
            SELECT {_DPE_SELECT_COLS},
            d.position,
            ST_Distance(
                parc.geometrie::geography, d.position::geography
            ) AS distance_m
            FROM diagnostics_dpe d
            JOIN parc ON ST_DWithin(
                parc.geometrie,
                d.position,
                :rayon_m / (111320.0 * GREATEST(COS(RADIANS(ST_Y(d.position))), 0.01))
            )
            WHERE d.type_batiment = 'appartement'
              -- A document kept without source geometry (DATA-02) is never
              -- attached spatially.
              AND d.position IS NOT NULL
            {filtre_cle}
        )
        SELECT {_cols("c")}
        FROM candidats c
        WHERE c.distance_m <= :rayon_m
          AND (
            c.distance_m = 0
            OR NOT EXISTS (
                SELECT 1 FROM parcelles p
                WHERE p.code_departement = {dept}
                  AND p.id_parcelle <> :id_parcelle
                  AND ST_DWithin(
                      p.geometrie,
                      c.position,
                      (c.distance_m + :marge_m)
                      / (111320.0 * GREATEST(COS(RADIANS(ST_Y(c.position))), 0.01))
                  )
                  AND ST_Distance(p.geometrie::geography, c.position::geography)
                      < c.distance_m + :marge_m
            )
          )
        ORDER BY c.date_etablissement_dpe DESC
        LIMIT :limit
    """)

    result = await session.execute(
        sql,
        {
            "id_parcelle": id_parcelle,
            "limit": limit,
            "rayon_m": SECOURS_RAYON_M,
            "marge_m": SNAP_MARGIN_M,
        },
    )
    return [dict(row._mapping) for row in result]


async def copropriete_par_parcelle(
    session: AsyncSession,
    id_parcelle: str,
    limit: int = 5,
    *,
    avec_gestion: bool = False,
) -> list[dict]:
    """Find copropriete registry entries for the exact cadastre parcel.

    Exact join on the parcels the registration declares (Plan 48-02, D-20)
    -- never a free-text address match. The registry cites up to three of
    them and the address may stand on any (KI-20): the second and the third
    are NULL until the import that follows migration 116. Bound LIMIT even
    though a parcel should carry at most one copropriete registration;
    defensive against duplicate ANAH filings.
    ``avec_gestion`` adds the manager and its mandate (the address report).
    """
    gestion = _COPROPRIETE_GESTION_COLS if avec_gestion else ""
    sql = text(f"""
        SELECT {_COPROPRIETE_SELECT_COLS}{gestion}
        FROM copropriete cp
        WHERE cp.id_parcelle_1 = :id_parcelle
           OR cp.id_parcelle_2 = :id_parcelle
           OR cp.id_parcelle_3 = :id_parcelle
        ORDER BY cp.nb_lots_total DESC NULLS LAST
        LIMIT :limit
    """)

    result = await session.execute(sql, {"id_parcelle": id_parcelle, "limit": limit})
    return [dict(row._mapping) for row in result]
