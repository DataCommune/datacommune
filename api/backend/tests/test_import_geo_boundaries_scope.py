"""Geographic referentials: communes national, sections bounded (02-11, 03-14).

``scripts/import_geo_boundaries.py`` imports the communes of every departement
the producer lists (``DEPARTEMENTS_LIST_URL``, R1 route A, plan 03-14) and
the cadastral sections and PLM arrondissements of the persistent scope only
(``load_scope_sync``, plan 02-11). Departement polygons stay national: the
transports import attributes stops and stations by polygon (02-05 m3); each
DROM polygon is the union of its communes (D-122, plan 04.1-06). An
empty list, a list without a scope departement, or a download that fails or
comes back empty stops the script (exit 1, code named) before any insertion;
with ``SOURCE_ARCHIVE_DIR``/``SOURCE_ARCHIVE_RUN_ID`` the raw responses,
list included, are archived before the first insertion (OPS-03 amended).

No network, no database: a fake HTTP client and a fake connection.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from app.etl.source_archive import SENTINEL_CONTENT, SENTINEL_NAME
from scripts import import_geo_boundaries as geo

RUN_ID = "20260924T220000Z"
NATIONAL = ["01", "44", "75", "79", "85"]
POLYGONS = ["01", "44", "75", "85", "79"]


def _square(x: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[x, 47.0], [x + 0.1, 47.0], [x + 0.1, 47.1], [x, 47.0]]],
    }


def _collection(features: list[dict]) -> bytes:
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def _listing(codes: list[str]) -> bytes:
    """The producer's departement list: a JSON array of {code, nom}."""
    return json.dumps([{"code": c, "nom": f"D{c}"} for c in codes]).encode()


def _departements(codes: list[str]) -> bytes:
    return _collection(
        [
            {
                "type": "Feature",
                "properties": {"code": c, "nom": f"D{c}"},
                "geometry": _square(i),
            }
            for i, c in enumerate(codes)
        ]
    )


def _communes(dept: str, n: int = 2) -> bytes:
    return _collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "code": f"{dept}{i:03d}",
                    "nom": f"C{dept}{i}",
                    "population": 10,
                    "codeDepartement": dept,
                },
                "geometry": _square(i),
            }
            for i in range(1, n + 1)
        ]
    )


def _arrondissements(dept: str, parent: str) -> bytes:
    return _collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "code": f"{dept}1{i:02d}",
                    "nom": f"A{i}",
                    "population": 5,
                    "codeDepartement": dept,
                    "codeParent": parent,
                },
                "geometry": _square(i),
            }
            for i in range(1, 3)
        ]
    )


def _sections(dept: str, n: int = 3) -> bytes:
    return gzip.compress(
        _collection(
            [
                {
                    "type": "Feature",
                    "properties": {
                        "id": f"{dept}001000A{i}",
                        "commune": f"{dept}001",
                        "prefixe": "000",
                        "code": f"A{i}",
                    },
                    "geometry": _square(i),
                }
                for i in range(n)
            ]
        )
    )


class _Resp:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status_code = status
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Serves canned bytes by URL; records every URL asked for."""

    def __init__(self, routes: dict[str, bytes | Exception | int]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def get(self, url: str, timeout=None) -> _Resp:
        self.urls.append(url)
        value = self.routes.get(url, 404)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, int):
            return _Resp(value)
        return _Resp(200, value)

    def close(self) -> None:
        pass


def _routes(
    scope: tuple[str, ...],
    polygons: list[str] | None = None,
    national: list[str] | None = None,
    **overrides,
) -> dict[str, bytes | Exception | int]:
    listed = NATIONAL if national is None else national
    routes: dict[str, bytes | Exception | int] = {
        geo.DEPARTEMENTS_LIST_URL: _listing(listed),
        geo.DEPARTEMENTS_URL: _departements(POLYGONS if polygons is None else polygons),
    }
    for d in listed:
        routes[geo.COMMUNES_URL.format(dept=d)] = _communes(d)
    for d in scope:
        routes[geo.SECTIONS_URL.format(dept=d)] = _sections(d)
    routes[geo.ARRONDISSEMENTS_URL.format(dept="75")] = _arrondissements("75", "75056")
    routes.update(overrides)
    return routes


class _Cursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.conn.on_insert(sql, params)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    """Scope rows, recorded inserts, per-departement counts computed from them."""

    def __init__(
        self,
        scope: tuple[str, ...],
        *,
        drop_inserts_for: str | None = None,
        before_first_insert=None,
    ) -> None:
        self.scope = scope
        self.drop_inserts_for = drop_inserts_for
        self.before_first_insert = before_first_insert
        self.inserts: list[tuple[str, tuple]] = []
        self.unions: list[tuple[str, tuple]] = []
        self.deletes: list[tuple[str, object]] = []
        self.committed = False
        self.rolled_back = False

    def on_insert(self, sql: str, params) -> None:
        table = re.search(r"INSERT INTO (\w+)", sql).group(1)
        if not self.inserts and self.before_first_insert is not None:
            self.before_first_insert()
        if table == "geo_departements" and "FROM geo_communes" in sql:
            # INSERT ... SELECT ... HAVING count(*) > 0: a row only when the
            # communes of the DROM are in the transaction.
            self.unions.append((sql, params))
            if params[2] not in self.inserted("geo_communes"):
                return
        dept = None if table == "geo_departements" else self._dept(table, params)
        if dept is not None and dept == self.drop_inserts_for:
            return
        self.inserts.append((table, params))

    @staticmethod
    def _dept(table: str, params) -> str:
        return params[3] if table == "geo_sections" else params[2]

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def execute(self, sql: str, params=None) -> _Result:
        if sql.lstrip().startswith("DELETE"):
            self.deletes.append((sql, params))
            return _Result([])
        if "instance_scope_datasets" in sql:
            rows = [("dataset", "ban")] + [("departement", d) for d in self.scope]
            return _Result(rows)
        if sql.startswith("SELECT code FROM geo_departements"):
            wanted = set(params[0])
            codes = {p[0] for t, p in self.inserts if t == "geo_departements"}
            return _Result([(c,) for c in sorted(codes & wanted)])
        match = re.search(
            r"SELECT departement_code, count\(\*\) FROM (\w+) GROUP BY 1", sql
        )
        if match:
            table = match.group(1)
            counts: dict[str, int] = {}
            for t, p in self.inserts:
                if t == table:
                    d = self._dept(table, p)
                    counts[d] = counts.get(d, 0) + 1
            return _Result(sorted(counts.items()))
        return _Result([])

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def inserted(self, table: str) -> set[str]:
        """Departements of the rows inserted into *table*."""
        return {self._dept(t, p) for t, p in self.inserts if t == table}


@pytest.fixture(autouse=True)
def _no_backfill(monkeypatch):
    """The superficie backfill and synthese rebuild need a real database."""
    monkeypatch.setattr(geo, "backfill_and_refresh_commune_synthese", lambda _c: None)
    monkeypatch.delenv("SOURCE_ARCHIVE_DIR", raising=False)
    monkeypatch.delenv("SOURCE_ARCHIVE_RUN_ID", raising=False)
    monkeypatch.delenv("SOURCE_ARCHIVE_MIN_FREE_BYTES", raising=False)


def _run(conn: FakeConn, client: FakeClient, **kw) -> int:
    try:
        geo.import_boundaries(conn, client, **kw)
    except geo.BoundaryImportError as exc:
        return exc.exit_code
    return 0


def _codes_called(client: FakeClient, template: str) -> set[str]:
    pattern = re.escape(template).replace(re.escape("{dept}"), r"(\w+)")
    return {m.group(1) for u in client.urls if (m := re.fullmatch(pattern, u))}


def _deleted_tables(conn: FakeConn) -> set[str]:
    return {re.search(r"DELETE FROM (\w+)", sql).group(1) for sql, _ in conn.deletes}


# ------------------------------------------------------------------ scope --


def test_communes_are_national_and_sections_stay_bounded_to_the_scope():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85")))
    assert _run(conn, client) == 0
    assert client.urls.count(geo.DEPARTEMENTS_LIST_URL) == 1
    assert client.urls.count(geo.DEPARTEMENTS_URL) == 1
    assert _codes_called(client, geo.COMMUNES_URL) == set(NATIONAL)
    # No section of 01, 75 or 79: the cadastre stays bounded to the scope.
    assert _codes_called(client, geo.SECTIONS_URL) == {"44", "85"}
    assert _codes_called(client, geo.ARRONDISSEMENTS_URL) == set()
    assert conn.inserted("geo_communes") == set(NATIONAL)
    assert conn.inserted("geo_sections") == {"44", "85"}
    assert conn.committed


def test_departement_polygons_stay_national():
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85")))) == 0
    polygons = sorted(p[0] for t, p in conn.inserts if t == "geo_departements")
    assert polygons == sorted(POLYGONS)


def test_polygons_missing_a_scope_departement_are_refused():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), polygons=["01", "44"]))
    assert _run(conn, client) == 1
    assert conn.inserts == []


def test_scope_departement_absent_from_the_producer_list_is_refused(capsys):
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), national=["01", "44", "79"]))
    with pytest.raises(SystemExit) as exc:
        geo.main_with(conn, client, [])
    assert exc.value.code == 1
    assert "85" in capsys.readouterr().out
    assert conn.inserts == []
    assert _codes_called(client, geo.COMMUNES_URL) == set()


def test_empty_producer_list_is_refused():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), national=[]))
    assert _run(conn, client) == 1
    assert conn.inserts == []
    assert client.urls == [geo.DEPARTEMENTS_LIST_URL]


def test_departements_option_outside_the_scope_loads_communes_only():
    # --departements 79: communes of the 79, no cadastre, others untouched.
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85")))
    assert _run(conn, client, only=["79"]) == 0
    assert _codes_called(client, geo.COMMUNES_URL) == {"79"}
    assert _codes_called(client, geo.SECTIONS_URL) == set()
    assert conn.inserted("geo_communes") == {"79"}
    assert conn.inserted("geo_sections") == set()
    assert _deleted_tables(conn) == {
        "geo_communes",
        "geo_arrondissements",
        "geo_sections",
    }
    for sql, params in conn.deletes:
        assert "WHERE departement_code = ANY(%s)" in sql, sql
        assert params == (["79"],), params
    assert conn.committed


def test_departements_option_keeps_the_other_departements():
    # --departements 85 must not wipe the communes and sections of the 44.
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85")))
    assert _run(conn, client, only=["85"]) == 0
    assert _codes_called(client, geo.COMMUNES_URL) == {"85"}
    assert _codes_called(client, geo.SECTIONS_URL) == {"85"}
    assert _deleted_tables(conn) == {
        "geo_communes",
        "geo_arrondissements",
        "geo_sections",
    }
    for sql, params in conn.deletes:
        assert "WHERE departement_code = ANY(%s)" in sql, sql
        assert params == (["85"],), params


def test_departements_option_unknown_to_the_producer_list_is_refused(capsys):
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85")))
    with pytest.raises(SystemExit) as exc:
        geo.main_with(conn, client, ["--departements", "99"])
    assert exc.value.code == 2
    assert "99" in capsys.readouterr().out
    assert client.urls == [geo.DEPARTEMENTS_LIST_URL]
    assert conn.inserts == []


def test_full_run_replaces_the_whole_referential():
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85")))) == 0
    assert len(conn.deletes) == 3
    assert all("WHERE" not in sql and params is None for sql, params in conn.deletes)


def test_empty_scope_is_refused():
    conn = FakeConn(())
    client = FakeClient({})
    assert _run(conn, client) == 2
    assert client.urls == []


# --------------------------------------------------------------- failures --


@pytest.mark.parametrize("failure", ["error", "empty"])
def test_communes_of_79_failed_or_empty_exit_1_naming_it(failure, capsys):
    url = geo.COMMUNES_URL.format(dept="79")
    value = RuntimeError("boom") if failure == "error" else _collection([])
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), **{url: value}))
    with pytest.raises(SystemExit) as exc:
        geo.main_with(conn, client, [])
    assert exc.value.code == 1
    assert "79" in capsys.readouterr().out
    assert conn.inserts == []
    assert not conn.committed


@pytest.mark.parametrize("kind", ["communes", "sections"])
def test_failed_download_of_85_exits_1_naming_it(kind, capsys):
    url = (geo.COMMUNES_URL if kind == "communes" else geo.SECTIONS_URL).format(
        dept="85"
    )
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), **{url: RuntimeError("boom")}))
    with pytest.raises(SystemExit) as exc:
        geo.main_with(conn, client, [])
    assert exc.value.code == 1
    assert "85" in capsys.readouterr().out
    assert conn.inserts == []
    assert not conn.committed


@pytest.mark.parametrize("kind", ["communes", "sections"])
def test_empty_response_of_85_exits_1(kind):
    if kind == "communes":
        override = {geo.COMMUNES_URL.format(dept="85"): _collection([])}
    else:
        override = {geo.SECTIONS_URL.format(dept="85"): gzip.compress(_collection([]))}
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85"), **override))) == 1
    assert conn.inserts == []


def test_sections_404_of_a_scope_departement_is_a_failure():
    override = {geo.SECTIONS_URL.format(dept="85"): 404}
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85"), **override))) == 1


@pytest.mark.parametrize("missing", ["85", "79"])
def test_final_count_refuses_a_missing_departement(missing):
    conn = FakeConn(("44", "85"), drop_inserts_for=missing)
    assert _run(conn, FakeClient(_routes(("44", "85")))) == 1
    assert not conn.committed
    assert conn.rolled_back


# ------------------------------------------------------- arrondissements --


def test_plm_departement_of_the_scope_loads_its_arrondissements():
    conn = FakeConn(("44", "75"))
    client = FakeClient(_routes(("44", "75")))
    assert _run(conn, client) == 0
    assert _codes_called(client, geo.ARRONDISSEMENTS_URL) == {"75"}
    rows = [p for t, p in conn.inserts if t == "geo_arrondissements"]
    assert [(p[0], p[3]) for p in rows] == [("75101", "75056"), ("75102", "75056")]
    # Arrondissements never enter the commune referential.
    assert not any(
        p[0].startswith("751") for t, p in conn.inserts if t == "geo_communes"
    )


def test_plm_departement_outside_the_scope_loads_no_arrondissements():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85")))
    assert _run(conn, client) == 0
    assert "75" in _codes_called(client, geo.COMMUNES_URL)
    assert _codes_called(client, geo.ARRONDISSEMENTS_URL) == set()
    assert not any(t == "geo_arrondissements" for t, _ in conn.inserts)


# ---------------------------------------------------------------- archive --


def _archive_root(tmp_path: Path, sentinel: bool = True) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    if sentinel:
        (root / SENTINEL_NAME).write_text(SENTINEL_CONTENT + "\n", encoding="utf-8")
    return root


def test_raw_responses_are_archived_before_the_first_insert(monkeypatch, tmp_path):
    root = _archive_root(tmp_path)
    monkeypatch.setenv("SOURCE_ARCHIVE_DIR", str(root))
    monkeypatch.setenv("SOURCE_ARCHIVE_RUN_ID", RUN_ID)
    monkeypatch.setenv("SOURCE_ARCHIVE_MIN_FREE_BYTES", "0")
    record = root / geo.ARCHIVE_DATASET / "runs" / f"{RUN_ID}.json"
    seen_at_first_insert: list[bool] = []
    conn = FakeConn(
        ("44", "85"),
        before_first_insert=lambda: seen_at_first_insert.append(record.is_file()),
    )
    routes = _routes(("44", "85"))
    assert _run(conn, FakeClient(routes)) == 0
    assert seen_at_first_insert == [True]
    written = json.loads(record.read_text(encoding="utf-8"))
    assert written["origin"] == "telechargement"
    assert written["run_id"] == RUN_ID
    # List + polygons + communes of each listed code + sections of the scope.
    assert len(written["files"]) == 1 + 1 + len(NATIONAL) + 2 == 9
    # Archived bytes are the bytes served (sections stay gzip-compressed).
    by_name = {f["name"]: f for f in written["files"]}
    assert by_name["sections-85.json.gz"]["bytes"] == len(
        routes[geo.SECTIONS_URL.format(dept="85")]
    )
    listing = routes[geo.DEPARTEMENTS_LIST_URL]
    assert by_name["departements-liste.json"]["bytes"] == len(listing)
    assert by_name["departements-liste.json"]["sha256"] == (
        hashlib.sha256(listing).hexdigest()
    )
    assert {f"communes-{d}.geojson" for d in NATIONAL} <= set(by_name)


def test_missing_sentinel_exits_1_without_inserting(monkeypatch, tmp_path):
    root = _archive_root(tmp_path, sentinel=False)
    monkeypatch.setenv("SOURCE_ARCHIVE_DIR", str(root))
    monkeypatch.setenv("SOURCE_ARCHIVE_RUN_ID", RUN_ID)
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85")))) == 1
    assert conn.inserts == []
    assert not (root / geo.ARCHIVE_DATASET).exists()


def test_without_archive_variables_nothing_is_archived(tmp_path):
    conn = FakeConn(("44", "85"))
    assert _run(conn, FakeClient(_routes(("44", "85")))) == 0
    assert conn.committed


# ------------------------------------------------------------------- DROM --

WITH_DROM = [*NATIONAL, "971", "976"]


def _polygon_codes(conn: FakeConn) -> list[str]:
    return [p[0] for t, p in conn.inserts if t == "geo_departements"]


def test_drom_polygons_are_built_from_their_communes():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), national=WITH_DROM))
    assert _run(conn, client) == 0
    # Metropolitan file unchanged, plus one polygon per DROM of the list.
    assert sorted(_polygon_codes(conn)) == sorted([*POLYGONS, "971", "976"])
    assert [params for _, params in conn.unions] == [
        ("971", "D971", "971"),
        ("976", "D976", "976"),
    ]
    for sql, _ in conn.unions:
        assert "ST_Multi" in sql and "ST_Union" in sql, sql
        assert "WHERE departement_code = %s" in sql, sql
        assert "ON CONFLICT (code) DO UPDATE" in sql, sql
    # Built after the communes of the same transaction were inserted.
    order = [t for t, _ in conn.inserts]
    last_commune = max(i for i, t in enumerate(order) if t == "geo_communes")
    first_drom = next(
        i
        for i, (t, p) in enumerate(conn.inserts)
        if t == "geo_departements" and p[0] == "971"
    )
    assert first_drom > last_commune
    assert conn.committed


def test_a_drom_of_the_scope_needs_no_metropolitan_polygon():
    # The polygon file has no DROM: a scope with 971 is not refused for it.
    conn = FakeConn(("44", "971"))
    client = FakeClient(_routes(("44", "971"), national=WITH_DROM))
    assert _run(conn, client) == 0
    assert "971" in _polygon_codes(conn)
    assert conn.inserted("geo_sections") == {"44", "971"}


def test_a_metropolitan_scope_departement_still_needs_its_polygon():
    conn = FakeConn(("44", "85"))
    routes = _routes(("44", "85"), polygons=["01", "44"], national=WITH_DROM)
    client = FakeClient(routes)
    assert _run(conn, client) == 1
    assert conn.inserts == []


def test_drom_without_communes_refuses_the_import():
    # The communes of 971 never reach the table: no polygon, rollback.
    conn = FakeConn(("44", "85"), drop_inserts_for="971")
    assert _run(conn, FakeClient(_routes(("44", "85"), national=WITH_DROM))) == 1
    assert "971" not in _polygon_codes(conn)
    assert conn.rolled_back
    assert not conn.committed


def test_drom_polygon_check_names_the_missing_drom():
    conn = FakeConn(("44", "85"))
    with pytest.raises(geo.BoundaryImportError, match="971"):
        geo.check_drom_polygons(conn, ["971"])
    assert geo.check_drom_polygons(conn, []) == []


def test_restricted_run_rebuilds_only_the_requested_drom():
    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), national=WITH_DROM))
    assert _run(conn, client, only=["976"]) == 0
    assert [params[0] for _, params in conn.unions] == ["976"]

    conn = FakeConn(("44", "85"))
    client = FakeClient(_routes(("44", "85"), national=WITH_DROM))
    assert _run(conn, client, only=["85"]) == 0
    assert conn.unions == []


def test_drom_list_matches_the_hosted_overseas_departements():
    from app.etl.base import DEPARTEMENTS

    overseas = [d for d in DEPARTEMENTS if d.startswith("97")]
    # 975 (Saint-Pierre-et-Miquelon) is a collectivity, not a DROM (D-123).
    assert [d for d in overseas if d != "975"] == list(geo.DROM_DEPARTEMENTS)
