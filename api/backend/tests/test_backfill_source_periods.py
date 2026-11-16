"""Backfill of the empty DVF and connectivite labels (C24, WINDOWS n° 86).

No database and no network: a fake connection keeps the three facts the
command reads (labels, DVF window, last connectivite run) and the producer is
an ``httpx.MockTransport``. Fixtures are synthetic.
"""

import hashlib
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from scripts import backfill_source_periods as backfill

pytestmark = pytest.mark.unit

ARCEP = "https://data.arcep.fr/fixe/maconnexioninternet"
S3 = "https://arcep.s3.rbx.io.cloud.ovh.net/fixe/maconnexioninternet"
CONTENU = {
    "actuel_44.csv.gz": b"eligibilite 44 du deuxieme trimestre",
    "base_imb_44.csv.gz": b"immeubles 44 du deuxieme trimestre",
}


def _journal(contenus: dict[str, bytes]) -> list[dict]:
    return [
        {
            "name": nom,
            "bytes": len(octets),
            "sha256": hashlib.sha256(octets).hexdigest(),
        }
        for nom, octets in contenus.items()
    ]


class FakeConn:
    """The rows the command reads, and the guarded UPDATE it may write."""

    def __init__(self, labels, window=(date(2021, 1, 2), date(2025, 12, 31)), run=None):
        self.labels = dict(labels)
        self.window = window
        self.run = run
        self.updates = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=()):
        if sql.startswith("SELECT source_period_label"):
            (dataset,) = params
            row = (self.labels[dataset],) if dataset in self.labels else None
            return SimpleNamespace(fetchone=lambda: row)
        if "date_mutation" in sql:
            assert "FROM mutations" in sql
            return SimpleNamespace(fetchone=lambda: self.window)
        if "FROM etl_runs" in sql:
            return SimpleNamespace(fetchone=lambda: self.run)
        if sql.startswith("UPDATE dataset_metadata"):
            label, dataset = params
            if self.labels.get(dataset):
                return SimpleNamespace(rowcount=0)
            self.labels[dataset] = label
            self.updates += 1
            return SimpleNamespace(rowcount=1)
        raise AssertionError(sql)


def _producteur(last="2026_T3", servis=None):
    """``last`` redirects to *last*; *servis*: quarter -> {name: bytes}."""
    servis = servis if servis is not None else {"2026_T2": CONTENU}
    lus: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        lus.append(f"{request.method} {url}")
        if "/last/" in url:
            cible = url.replace(ARCEP, S3).replace("/last/", f"/{last}/")
            return httpx.Response(302, headers={"Location": cible})
        for trimestre, fichiers in servis.items():
            for nom, octets in fichiers.items():
                if f"/{trimestre}/" in url and url.endswith(nom):
                    return httpx.Response(200, content=octets)
        return httpx.Response(200, content=b"autre contenu")

    def factory():
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory, lus


def _main(conn, argv=(), producteur=None):
    factory, lus = producteur or _producteur()
    code = backfill.main(list(argv), connect=lambda _dsn: conn, client_factory=factory)
    return code, lus


@pytest.mark.parametrize(
    ("avant", "derive", "action"),
    [
        (None, "x", "to_fill"),
        ("", "x", "to_fill"),
        (None, None, "unproved"),
        ("x", "x", "unchanged"),
        ("x", None, "unchanged"),
        ("x", "y", "differs"),
    ],
)
def test_a_label_is_filled_only_when_empty_and_never_invented(avant, derive, action):
    assert backfill.decide(avant, derive) == action


def test_the_quarters_tried_go_back_across_the_year():
    assert backfill.previous_quarters("2026_T2", 3) == ["2026_T2", "2026_T1", "2025_T4"]
    assert backfill.previous_quarters("last", 3) == []


def test_apply_fills_both_labels_then_writes_nothing(capsys):
    conn = FakeConn({"dvf": None, "connectivite": None}, run=(36, _journal(CONTENU)))

    premier, lus = _main(conn, ["--apply"])
    second, relus = _main(conn, ["--apply"])

    assert (premier, second) == (backfill.EXIT_OK, backfill.EXIT_OK)
    assert conn.labels == {
        "dvf": "2021-01-02 → 2025-12-31",
        "connectivite": "2026_T2",
    }
    assert conn.updates == 2
    # The quarter behind `last` (T3) does not serve the journaled bytes; T2 does.
    assert any("/2026_T3/" in ligne for ligne in lus)
    sortie = capsys.readouterr().out
    assert "BACKFILL connectivite avant=NULL derive=2026_T2 action=filled" in sortie
    assert "BACKFILL dvf avant=2021-01-02 → 2025-12-31" in sortie
    # Second pass: the stored label is kept, the producer is not even read.
    assert relus == []


def test_without_apply_nothing_is_written(capsys):
    conn = FakeConn({"dvf": None, "connectivite": None}, run=(36, _journal(CONTENU)))

    code, _ = _main(conn)

    assert code == backfill.EXIT_OK
    assert conn.updates == 0
    assert "action=to_fill" in capsys.readouterr().out


def test_no_quarter_serving_every_file_writes_no_period(capsys):
    altere = dict(CONTENU, **{"base_imb_44.csv.gz": b"autre trimestre"})
    conn = FakeConn({"dvf": None, "connectivite": None}, run=(36, _journal(altere)))

    code, _ = _main(conn, ["--apply"])

    assert code == backfill.EXIT_UNPROVED
    assert conn.labels["connectivite"] is None
    assert conn.labels["dvf"] == "2021-01-02 → 2025-12-31"
    assert "action=unproved" in capsys.readouterr().out


def test_a_present_label_that_differs_is_refused_never_overwritten():
    conn = FakeConn({"dvf": "2020-2024", "connectivite": "2026_T1"})

    code, lus = _main(conn, ["--apply"])

    assert code == backfill.EXIT_DIFFERS
    assert conn.labels == {"dvf": "2020-2024", "connectivite": "2026_T1"}
    assert conn.updates == 0
    assert lus == []


def test_an_empty_dvf_table_and_no_connectivite_run_prove_nothing():
    conn = FakeConn({"dvf": None, "connectivite": None}, window=(None, None))

    code, _ = _main(conn, ["--apply"])

    assert code == backfill.EXIT_UNPROVED
    assert conn.labels == {"dvf": None, "connectivite": None}
