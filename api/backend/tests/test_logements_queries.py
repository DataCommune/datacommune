"""Tests: logements data-access layer (Phase 48, Plan 05 -- D-10).

The DPE-per-parcel lookup is the query the whole phase can most easily get
wrong: the only existing DPE path is ``dpe_par_point`` (500 m radius), and
using it here would resurface neighbours' DPE rows as if they belonged to
this building -- exactly the "invented list" regression Phase 45 fixed for
DVF. These tests prove, without a DB, that the queries:

- attach DPE by the EXACT BAN key first (``identifiant_ban = :ban_id``, no
  geometry at all -- RESEARCH-L1-RATTACHEMENT-DPE-2026-07-31, step 2);
- widen that key set from the LOCAL ``rnb_liens`` table (Lot C1), keeping the
  parcel/building partition in the SQL itself -- the row-level semantics of
  that join (carrier partition, multi-carrier union) are proven against a real
  PostGIS in ``tests/integration/test_rnb_liens_elargissement.py``, which is
  the only place they CAN be proven: they are properties of the SQL, not of
  the Python around it;
- keep the spatial path BOUNDED as a fallback: 12 m, the resolve snap's 3 m
  non-ambiguity margin, containment still unconditional, and never the 500 m
  point radius (ST_MakePoint/ST_Buffer absent);
- filter on ``type_batiment = 'appartement'`` (an "immeuble" DPE is never a
  logement card, Pitfall 5);
- bind every parameter (id_parcelle, ban_id, limit) -- never inline a value;
- derive the partition key the same way as the certified DVF queries;
- never touch the shared, certified queries (``dpe_par_point``,
  ``dvf_par_id_parcelle``, ``copropriete_par_codes``).

Pattern: ``_FakeResult``/``_CaptureSession`` copied verbatim from
``tests/test_dvf_minimization.py``.
"""

import inspect

import pytest
from sqlalchemy.exc import ProgrammingError

from app.dpe import queries as dpe_queries
from app.logements import queries as logements_queries
from app.resolve import snap

# asyncio_mode = "auto" (pyproject.toml) already drives the async test
# functions below; pytest.mark.asyncio is therefore omitted here (applying it
# module-wide would wrongly tag the one sync test in this file, see
# test_aucune_requete_dpe_immeuble).
pytestmark = pytest.mark.unit


async def _acted(_session, _tables):
    return True


@pytest.fixture(autouse=True)
def _publication_acted(monkeypatch):
    """The fake sessions answer the widening query only: the publication guard
    of ``rnb_liens`` is proven on PostgreSQL
    (``tests/integration/test_rnb_liens_elargissement.py``)."""
    monkeypatch.setattr(logements_queries, "publication_actee", _acted)


class _FakeResult:
    def __iter__(self):
        return iter(())


class _CaptureSession:
    def __init__(self, captured: dict):
        self._captured = captured

    async def execute(self, sql, params):
        self._captured["sql"] = str(sql)
        self._captured["params"] = params
        return _FakeResult()


class _RowsSession:
    """Session returning fabricated rows (no DB, no SQL engine).

    The widening query projects ``(cle_ban, largeur_porteur)`` -- the second
    column is the width of the widest carrier, duplicated on every row.
    """

    def __init__(self, rows: list[tuple]):
        self._rows = rows

    async def execute(self, sql, params):
        return iter(self._rows)


class _ExplodingSession:
    """Session whose execute() fails the way a broken query would."""

    async def execute(self, sql, params):
        raise ProgrammingError("SELECT 1", {}, Exception('relation "rnb_liens"'))


ID_PARCELLE = "85047000AB0042"
BAN_ID = "44109_zz01_00002"


async def test_cles_batiment_scope_la_parcelle_et_exclut_la_cle_demandee():
    """Lot C1: the two load-bearing clauses of the widening are in the SQL.

    - ``batiments_parcelle`` restricts the carriers to the buildings of THIS
      parcel: without it the join would leave the parcel entirely (the
      ``cle_seule`` variant the equivalence gate measured as strictly worse,
      204 vs 218 identical fiches);
    - ``cle_ban <> :ban_id`` is a contract, not an optimisation:
      ``marque_rattachement_ban`` tells ``exact`` from ``elargi_batiment``
      by this exclusion alone.
    """
    captured: dict = {}
    await logements_queries.cles_ban_batiment_rnb(
        _CaptureSession(captured), ID_PARCELLE, BAN_ID
    )

    sql = captured["sql"]
    assert "batiments_parcelle" in sql, sql
    assert "porteurs" in sql, sql
    assert "l.cle_ban <> :ban_id" in sql, sql


async def test_cles_batiment_utilise_l_operateur_gin():
    """``@>`` is what idx_rnb_liens_parcelles answers -- ``= ANY`` is a seq scan.

    1,5 M rows read on the hot path of a public endpoint: the containment
    operator is not a style choice.
    """
    captured: dict = {}
    await logements_queries.cles_ban_batiment_rnb(
        _CaptureSession(captured), ID_PARCELLE, BAN_ID
    )

    sql = captured["sql"]
    assert "parcelles @> ARRAY[:id_parcelle]::text[]" in sql, sql
    assert "= ANY" not in sql, sql


async def test_cles_batiment_ne_restreint_pas_a_un_seul_porteur():
    """Decision 2026-08-05: UNION of every carrier, never the first one.

    The live RNB call this replaced returned the keys of whichever carrier the
    upstream listed first -- order-dependent AND under-collecting. Any LIMIT
    or single-row selection in the carrier CTE would re-introduce exactly
    that. Guarded on the SQL because that is where the union is decided (the
    row-level proof lives in tests/integration/).
    """
    captured: dict = {}
    await logements_queries.cles_ban_batiment_rnb(
        _CaptureSession(captured), ID_PARCELLE, BAN_ID
    )

    sql = captured["sql"]
    assert "LIMIT" not in sql.upper(), sql
    assert "JOIN porteurs p ON p.id_rnb = l.id_rnb" in sql, sql


async def test_cles_batiment_bind_ses_parametres():
    """Neither the parcel id nor the BAN key is ever inlined in the SQL."""
    captured: dict = {}
    await logements_queries.cles_ban_batiment_rnb(
        _CaptureSession(captured), ID_PARCELLE, BAN_ID
    )

    sql = captured["sql"]
    params = captured["params"]
    assert ID_PARCELLE not in sql and BAN_ID not in sql, sql
    assert params == {"id_parcelle": ID_PARCELLE, "ban_id": BAN_ID}, params


async def test_cles_batiment_trie_et_dedoublonne():
    """Reproducible tuple: sorted, deduped, empty values dropped."""
    # The fictitious key comes first: rows given in sorted order would let an
    # unsorted (first-seen) dedupe pass.
    rows = [
        ("44109_zz01_00099", 3),
        ("44109_3056_00012", 3),
        ("44109_3056_00012", 3),
        (None, 3),
    ]

    cles = await logements_queries.cles_ban_batiment_rnb(
        _RowsSession(rows), ID_PARCELLE, BAN_ID
    )

    assert cles == ("44109_3056_00012", "44109_zz01_00099")


async def test_cles_batiment_applique_le_plafond_de_largeur():
    """2026-08-06: an aberrant RNB aggregate widens NOTHING, not "less".

    The ceiling is a pure service function, exhaustively pinned in
    tests/test_logements_plafond_largeur.py; what is proven here is that the
    query actually applies it, so no caller can obtain a raw widening. A
    16-key carrier (the gallery artefact) is well past the 9-key width
    ceiling, however few keys the fiche actually receives.
    """
    rows = [
        ("44109_zz01_00004", 16),
        ("44109_zz01_00006", 16),
        ("44109_3236_00012", 16),
        ("44109_3236_00014", 16),
    ]

    cles = await logements_queries.cles_ban_batiment_rnb(
        _RowsSession(rows), ID_PARCELLE, BAN_ID
    )

    assert cles == ()


async def test_cles_batiment_laisse_passer_l_immeuble_d_angle(caplog):
    """Non-regression R4-02: a 3-key carrier on 3 streets widens, logs nothing.

    The 3-street shape is the recalibration case itself (angle-44-000006,
    blocked by the previous ceiling): it must stay invisible in the trace --
    a logged "ceiling" line on a legitimate corner building would make the
    production trace unreadable.
    """
    with caplog.at_level("INFO", logger="app.logements.queries"):
        cles = await logements_queries.cles_ban_batiment_rnb(
            _RowsSession([("44109_3236_00012", 3), ("44109_5000_00007", 3)]),
            ID_PARCELLE,
            BAN_ID,
        )

    assert cles == ("44109_3236_00012", "44109_5000_00007")
    assert caplog.records == []


async def test_cles_batiment_trace_le_plafond_sans_publier_de_cle_ban(caplog):
    """A BAN key is personal data here: the trace carries COUNTS only.

    The line must state how wide the widest carrier was and how many keys
    were dropped -- and hold neither the resolved key nor any widened one.
    """
    rows = [("44109_5000_00001", 12), ("44109_5001_00001", 12)]

    with caplog.at_level("INFO", logger="app.logements.queries"):
        cles = await logements_queries.cles_ban_batiment_rnb(
            _RowsSession(rows), ID_PARCELLE, BAN_ID
        )

    assert cles == ()
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "porteur le plus large 12 cles" in message, message
    assert "2 cles ecartees" in message, message
    assert BAN_ID not in message, message
    for cle, _largeur in rows:
        assert cle not in message, message
    """Empty table / unknown parcel -> (), i.e. strict single-key behaviour.

    Degradation is silent for the caller and never an exception: the
    attachment itself still runs on the resolved key.
    """
    assert (
        await logements_queries.cles_ban_batiment_rnb(
            _RowsSession([]), ID_PARCELLE, BAN_ID
        )
        == ()
    )


async def test_cles_batiment_court_circuite_sans_cle():
    """No BAN key -> no widening and, above all, no round trip to the DB."""
    vide: dict = {}
    assert (
        await logements_queries.cles_ban_batiment_rnb(
            _CaptureSession(vide), ID_PARCELLE, ""
        )
        == ()
    )
    assert "sql" not in vide, vide


async def test_cles_batiment_ne_masque_pas_une_erreur_sql():
    """rnb_liens is OUR table: a broken query is a bug of ours, not an outage.

    Non-regression on Lot C1's removal of the fail-open wrapper: that
    ADR-0004 carve-out existed for a THIRD-PARTY upstream. Swallowing a local
    SQL error would hide a missing table or a botched migration behind a
    quietly narrower fiche.
    """
    with pytest.raises(ProgrammingError):
        await logements_queries.cles_ban_batiment_rnb(
            _ExplodingSession(), ID_PARCELLE, BAN_ID
        )


async def test_dpe_par_ban_joins_on_the_exact_key():
    """L1 step 2: the primary path is an equality on the BAN key, no geometry.

    The whole point of the fix: an ADEME DPE position IS the BAN entrance
    point, so it frequently sits outside the parcel (2 rue des Essais: 10 rows by
    key, 0 by ST_Intersects). The key path must therefore carry no spatial
    predicate at all.
    """
    captured: dict = {}
    await logements_queries.dpe_appartements_par_ban(
        _CaptureSession(captured), [BAN_ID]
    )

    sql = captured["sql"]
    assert "d.identifiant_ban IN" in sql, sql
    for spatial in ("ST_Intersects", "ST_DWithin", "ST_MakePoint", "ST_Buffer"):
        assert spatial not in sql, sql
    assert "parcelles" not in sql, sql


async def test_dpe_par_ban_binds_key_and_limit_and_filters_appartement():
    """Bound parameters only, appartement filter, hard LIMIT (Pitfall 3/5)."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_ban(
        _CaptureSession(captured), [BAN_ID], limit=42
    )

    sql = captured["sql"]
    params = captured["params"]
    assert BAN_ID not in sql, "ban keys must be bound, not inlined"
    assert params["ban_ids"] == [BAN_ID], params
    assert params["limit"] == 42, params
    assert "LIMIT :limit" in sql, sql
    assert "type_batiment = 'appartement'" in sql, sql


async def test_dpe_par_ban_derives_partition_from_the_key():
    """The BAN key starts with the INSEE code -> same Pattern B pruning.

    R4-02: the derivation moved to Python (_dept_depuis_cle) because the
    widened path binds a LIST of keys -- the partition prune must survive as
    a bound ``code_departement IN`` predicate covering every key's prefix.
    """
    captured: dict = {}
    await logements_queries.dpe_appartements_par_ban(
        _CaptureSession(captured), [BAN_ID, "97411_0123_00007"]
    )

    sql = captured["sql"]
    assert "d.code_departement IN" in sql, sql
    assert captured["params"]["depts"] == ["44", "974"], captured["params"]


async def test_dpe_par_ban_union_dedupes_and_short_circuits_empty():
    """R4-02 hygiene: duplicate/empty keys collapse; no keys -> no query."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_ban(
        _CaptureSession(captured), [BAN_ID, BAN_ID, ""]
    )
    assert captured["params"]["ban_ids"] == [BAN_ID], captured["params"]

    vide: dict = {}
    rows = await logements_queries.dpe_appartements_par_ban(_CaptureSession(vide), [])
    assert rows == []
    assert "sql" not in vide, "empty key list must never hit the DB"


async def test_dpe_par_ban_never_filters_on_statut_geocodage():
    """RESEARCH-L1 §1.3: that ADEME flag is incoherent, filtering loses 24 %."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_ban(
        _CaptureSession(captured), [BAN_ID]
    )

    assert "statut_geocodage" not in captured["sql"]


async def test_dpe_appartements_query_uses_spatial_join():
    """The fallback list is still built from the parcel geometry, never a point."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    assert "parc.geometrie" in sql, sql
    assert "parcelles" in sql, sql


async def test_dpe_appartements_fallback_is_bounded_and_unambiguous():
    """The tolerated radius stays bounded AND arbitrated by the snap margin.

    Replaces the former "no ST_DWithin at all" assertion: the strict
    ST_Intersects was the L1 bug (BAN points land on the roadway). The Phase 45
    guarantee is now carried by three properties instead -- a 12 m bound, the
    resolve snap's >=3 m margin over every other parcel, and the fact that the
    500 m point-radius primitives are still absent.
    """
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "ST_DWithin" in sql, sql
    assert ":rayon_m" in sql and ":marge_m" in sql, sql
    assert params["rayon_m"] == logements_queries.SECOURS_RAYON_M
    assert params["rayon_m"] == 12.0, params
    assert params["marge_m"] == snap.SNAP_MARGIN_M
    assert params["marge_m"] == 3.0, params
    # No point-radius primitive: the 500 m dpe_par_point path stays excluded.
    assert "ST_MakePoint" not in sql, sql
    assert "ST_Buffer" not in sql, sql
    # A tie attaches nothing (same doctrine as select_snapped_candidates).
    assert "NOT EXISTS" in sql, sql
    assert "p.id_parcelle <> :id_parcelle" in sql, sql


async def test_dpe_appartements_fallback_keeps_containment_unconditional():
    """A DPE point INSIDE the parcel is never disqualified by the margin.

    Non-regression on the pre-fix behaviour: adjacent parcels in a dense block
    sit well under 3 m, so applying the margin to contained points would drop
    rows that ST_Intersects used to attach with certainty.
    """
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    assert "c.distance_m = 0" in captured["sql"], captured["sql"]


async def test_dpe_appartements_fallback_can_restrict_to_keyless_rows():
    """Completion mode: only rows the exact BAN path cannot carry."""
    sans: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(sans), ID_PARCELLE
    )
    avec: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(avec), ID_PARCELLE, seulement_sans_cle=True
    )

    assert "identifiant_ban IS NULL" not in sans["sql"], sans["sql"]
    assert "d.identifiant_ban IS NULL" in avec["sql"], avec["sql"]


async def test_dpe_appartements_query_filters_type_batiment():
    """Pitfall 5: a collective ('immeuble') DPE is never a logement card."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    assert "type_batiment = 'appartement'" in sql, sql


async def test_dpe_appartements_query_has_bound_limit():
    """Pitfall 3: hard bound LIMIT (324 appartement DPE observed on one address)."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE, limit=42
    )

    sql = captured["sql"]
    params = captured["params"]
    assert "LIMIT :limit" in sql, sql
    assert params["limit"] == 42, params
    assert isinstance(params["limit"], int)


async def test_dpe_appartements_query_binds_id_parcelle():
    """The parcel id never appears inline in the SQL text -- only in params."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    params = captured["params"]
    assert ID_PARCELLE not in sql, "id_parcelle must be bound, not inlined"
    assert params["id_parcelle"] == ID_PARCELLE, params


async def test_dpe_appartements_query_derives_partition():
    """Same partition derivation as the certified DVF queries (Pattern B)."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    assert "WHEN LEFT(:id_parcelle, 2) = '97' THEN LEFT(:id_parcelle, 3)" in sql, sql


async def test_dpe_appartements_query_projects_logement_columns():
    """The 5 logement columns added by Plan 48-01 must all be projected."""
    captured: dict = {}
    await logements_queries.dpe_appartements_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    for col in (
        "complement_adresse_logement",
        "numero_etage_appartement",
        "date_fin_validite_dpe",
        "numero_dpe_remplace",
    ):
        assert col in sql, f"{col} missing from projection: {sql}"


def test_aucune_requete_dpe_immeuble():
    """No function in this module fetches 'immeuble'-scale DPE rows.

    No surface of the approved UI contract renders a building-scale diagnostic
    line, and the honest denominator is computed on logements -- a query with
    no consumer would be debt, not preparation (must_haves, Pitfall 5).
    """
    members = inspect.getmembers(logements_queries, inspect.isfunction)
    names = [name for name, _ in members]
    assert not any("immeuble" in name for name in names), names


async def test_copropriete_par_parcelle_uses_id_parcelle_1():
    """D-20: the parcel-exact join, never a free-text address match."""
    captured: dict = {}
    await logements_queries.copropriete_par_parcelle(
        _CaptureSession(captured), ID_PARCELLE
    )

    sql = captured["sql"]
    assert "id_parcelle_1 = :id_parcelle" in sql, sql
    # A registration declares up to three parcels: each is an exact join.
    assert "id_parcelle_2 = :id_parcelle" in sql, sql
    assert "id_parcelle_3 = :id_parcelle" in sql, sql
    assert "LIKE" not in sql.upper(), "no fuzzy address matching allowed here"


async def test_no_shared_query_touched():
    """Non-regression: app.dpe.queries.dpe_par_point keeps its point-radius shape."""
    captured: dict = {}
    await dpe_queries.dpe_par_point(_CaptureSession(captured), lat=46.83, lon=-1.9)

    sql = captured["sql"]
    assert "ST_Intersects" not in sql, sql
    assert "parcelles" not in sql, sql
