"""Nearest-parcel snapping rule for points falling outside every parcel.

Real case that motivated it: a BAN entrance point of a score-1.0 address in
Nantes sits on the roadway, strictly outside the abutting parcel (measured
0.0 m to the closest parcel, 9.6 m to the runner-up). ``ST_Intersects``
therefore returns zero candidate, ``id_parcelle`` stays ``null`` and every
parcel-keyed feature downstream is silently disabled.

This rule runs ONLY when the strict point-in-polygon lookup found nothing,
and it keeps the Phase 45 vocabulary (D-04): a clear winner is ``exact``,
a tie stays ``ambiguous`` — never a silent pick among close neighbours.
"""

# Search radius (metres) of the fallback nearby-parcel lookup.
SNAP_RADIUS_M = 10.0

# Minimum gap (metres) between the two nearest parcels for the closest one
# to count as a clear winner instead of a tie.
SNAP_MARGIN_M = 3.0


def select_snapped_candidates(candidates: list[dict]) -> list[dict]:
    """Decide which nearby parcels may be exposed as the canonical identity.

    ``candidates`` are rows of :func:`app.cadastre.queries.parcelles_proches`,
    already ordered by increasing ``distance_m``. Cardinality rule:

    - 0 candidate  -> empty list (typed absence, behaviour unchanged);
    - 1 candidate  -> that parcel alone (exact);
    - n candidates -> the closest one alone when it leads the runner-up by at
      least :data:`SNAP_MARGIN_M`, otherwise the whole list (ambiguous),
      re-sorted on ``id_parcelle`` to keep the stable order the strict lookup
      already exposes.

    A missing ``distance_m`` is treated as a tie (fail-closed): an unmeasured
    candidate never wins the snap.
    """
    if len(candidates) < 2:
        return list(candidates)

    closest = candidates[0].get("distance_m")
    runner_up = candidates[1].get("distance_m")
    if (
        closest is not None
        and runner_up is not None
        and runner_up - closest >= SNAP_MARGIN_M
    ):
        return [candidates[0]]
    return sorted(candidates, key=lambda row: row["id_parcelle"])
