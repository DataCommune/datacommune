"""Local 44+85 run of the BDNB import against the real exports (04.7-19 T6).

Opt-in, never in CI: skipped without ``DC_BDNB_ZIPS_DIR``. Disposable wrapper
database only. The real runner, pipeline, swap SQL and producer floors
(``app/scope/producer_counts.py``, not set aside here) load the producer's
departmental zips of millesime ``2026-02-b``; the HTTP download is replaced by
a copy of the local zip under its canonical name, nothing goes to the network.

Environment (absolute paths):
  - ``DC_BDNB_ZIPS_DIR``: ``dep85.zip`` (mandatory), ``dep44.zip`` if present;
  - ``DC_BDNB_PROOF_DIR``: where the JSON proofs are written, mandatory as
    soon as the first one is set;
  - ``DC_BDNB_ATTENDU``: output of ``scripts/bdnb_equivalence.py attendu``;
    without it, the reader = rule part is skipped with its reason.

Checks: the receipt (``export_import_manifest.py``); fidelity ``count
--archive-run`` / ``loaded`` / ``diff``, every compared metric ``egal`` per
departement and the ``millesime`` block equal, raw metrics = receipt; the 85
counts of the research read again in the file and in the base; published rows
= relations minus each named gap; ``ffo_divergents`` 0; ``2026-02.b``
everywhere; the reader of the theme = ``attendu`` on the whole sample; the
``construction`` theme on two real parcels; ``EXPLAIN`` on
``idx_bdnb_bp_parcelle_groupe`` with the p50/p95 of the reader; sizes
(``measure_db_sizes.py``) and the distribution of the years.

Run via the wrapper script, never directly (PowerShell, from the repo root):
    $env:DC_BDNB_ZIPS_DIR = 'C:/tmp/bdnb-19/raw'
    $env:DC_BDNB_PROOF_DIR = '<abs>/mesures-19/local'
    $env:DC_BDNB_ATTENDU = '<abs>/mesures-19/attendu.json'
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_bdnb_zips_locaux.py
"""

import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import statistics
import subprocess
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.adresse.schemas import (
    CommuneRef,
    DepartementRef,
    IdentiteAdresse,
    RelationParcelle,
)
from app.config import settings
from app.etl import source_archive as sa
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_bdnb import pipeline as bdnb_pipeline
from app.etl_bdnb.config import MEMBER_RELATIONS
from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
from app.rapport.service import lire_theme
from app.rapport.socle import ContexteRapport, Memo
from app.rapport.themes import construction
from app.scope import load_scope_sync
from app.scope.dependencies import route_hosting
from scripts import export_import_manifest as eim
from scripts import fidelite_nationale as fn
from scripts import measure_db_sizes as msz
from tests.integration import test_bdnb_pipeline as base
from tests.integration.instance_scope_fixture import install_instance_scope
from tests.integration.test_publication_restore import _command, _owned_container

ZIPS_DIR = os.environ.get("DC_BDNB_ZIPS_DIR", "")

pytestmark = [
    pytest.mark.destructive,
    pytest.mark.skipif(
        not ZIPS_DIR,
        reason="DC_BDNB_ZIPS_DIR absent: local run against the real BDNB "
        "exports, opt-in (plan 04.7-19 T6), never in CI",
    ),
]

TABLE = base.TABLE
INDEX = "idx_bdnb_bp_parcelle_groupe"
MILLESIME_URL = "2026-02-b"
LABEL = "2026-02.b"
# Content-Length of the producer objects read by the research (02/11).
CONTENT_LENGTH = {"85": 605_427_611, "44": 781_478_787}
REPO_ROOT = Path(__file__).resolve().parents[4]

# 04.7-19-RESEARCH § 4, read in csv/rel_batiment_groupe_parcelle.csv of dep85.
RECHERCHE_85 = {
    "relations": 615_723,
    "ffo": 402_191,
    "parcelles_distinctes": 493_069,
    "groupes_par_parcelle": {1: 378_204, 2: 109_424, 3: 4_354},
}
# The research wrote « ≥ 11 → 35 »; read again on 2026-11-02 in the same zip,
# 35 is the count beyond eleven groups (≥ 12), and 38 parcels have eleven or
# more (3 have exactly 11). Both are asserted, under their true bounds.
RECHERCHE_85_AU_MOINS_12 = 35
MESURE_85_AU_MOINS_11 = 38

LUCON = {
    "ban_id": "85128_zz03_00004",
    "commune": "85128",
    "parcelle": "85128000ZZ0655",
    "groupe": "bdnb-bg-3NNY-FXQN-65KX",
}
DEUX_GROUPES = {"commune": "85172", "parcelle": "85172000AE0114"}
CHAMPS_ATTENDU = {
    "batiment_groupe_id",
    "annee_construction",
    "mat_mur_txt",
    "mat_toit_txt",
    "parcelle_principale",
}
REPETITIONS = 3

# Fixture of the pipeline tests: migrated child database (migration first).
migrated = base.migrated


def _chemin_absolu(variable: str, *, obligatoire: bool) -> Path | None:
    value = os.environ.get(variable, "")
    if not value:
        if obligatoire:
            pytest.fail(f"{variable} is mandatory once DC_BDNB_ZIPS_DIR is set")
        return None
    path = Path(value)
    if not path.is_absolute():
        pytest.fail(f"{variable} must be an absolute path, got {value!r}")
    return path


def _ecrire(directory: Path, name: str, payload: Any) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cli(argv: list[str]) -> tuple[int, dict]:
    """``fidelite_nationale.main``: it writes its JSON to ``sys.stdout.buffer``."""
    buffer = io.BytesIO()
    out = io.TextIOWrapper(buffer, encoding="utf-8")
    with contextlib.redirect_stdout(out):
        code = fn.main(argv)
    out.flush()
    return code, json.loads(buffer.getvalue().decode("utf-8"))


def _async_dsn(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+asyncpg://")


def _stored_scope(dsn):
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        return load_scope_sync(conn)


# --- The load, once for the module -------------------------------------------


@dataclass
class Charge:
    dsn: str
    preuves: Path
    attendu: Path | None
    zips: dict[str, Path]
    departements: tuple[str, ...]
    archive: Path
    record: Path
    run_id: str
    counters: dict[str, int]


def _zips_locaux() -> dict[str, Path]:
    directory = Path(ZIPS_DIR)
    if not directory.is_absolute():
        pytest.fail(f"DC_BDNB_ZIPS_DIR must be an absolute path, got {ZIPS_DIR!r}")
    zips = {"85": directory / "dep85.zip"}
    if (directory / "dep44.zip").is_file():
        zips["44"] = directory / "dep44.zip"
    assert zips["85"].is_file(), f"dep85.zip absent of {directory}"
    # The local copy is the producer object: same size as its Content-Length.
    sizes = {dept: path.stat().st_size for dept, path in zips.items()}
    assert sizes == {dept: CONTENT_LENGTH[dept] for dept in zips}
    return zips


def _serve_local(monkeypatch, zips: dict[str, Path], asked: list[str]) -> None:
    """Replace the HTTP download by a copy of the local zip, canonical name."""

    async def copy(_client, url, output_path, **_kwargs):
        asked.append(url)
        assert f"bdnb_millesime_{MILLESIME_URL}/" in url, url
        dept = url.rsplit("_dep", 1)[1].split("_", 1)[0].upper()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(zips[dept], output_path)
        return True

    monkeypatch.setattr(bdnb_pipeline, "verified_download", copy)


def _container_free_bytes(dsn: str, audit: list) -> int:
    """Free bytes of the wrapper container's data directory (tmpfs), as
    ``_scoped_runner`` of test_l09_lovac_full_replay.py measures them."""
    container = _owned_container(dsn, audit)
    output = _command(
        [
            "docker",
            "exec",
            container,
            "df",
            "-B1",
            "--output=avail",
            "/var/lib/postgresql/data",
        ],
        audit,
    )
    return int(output.decode().splitlines()[-1])


def _archive_root(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    return directory


def _counts(result) -> dict[str, int]:
    return {
        stage.stage_name.removeprefix("count:"): stage.rows_affected
        for stage in result.stages
        if stage.stage_name.startswith("count:")
    }


def _repo_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def charge(migrated, tmp_path_factory):
    preuves = _chemin_absolu("DC_BDNB_PROOF_DIR", obligatoire=True)
    assert preuves is not None
    attendu = _chemin_absolu("DC_BDNB_ATTENDU", obligatoire=False)
    if attendu is not None:
        assert attendu.is_file(), f"DC_BDNB_ATTENDU names no file: {attendu}"
    zips = _zips_locaux()
    departements = tuple(sorted(zips))
    work = tmp_path_factory.mktemp("bdnb_zips_locaux")
    archive = _archive_root(work / "archive")
    audit: list = []
    asked: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", migrated)
        mp.setattr(settings, "database_url", migrated)
        mp.setattr(settings, "etl_temp_dir", str(work / "etl"))
        mp.setattr(settings, "source_archive_dir", str(archive))
        free = _container_free_bytes(migrated, audit)
        mp.setattr(
            PipelineRunner,
            "_gate03_free_bytes",
            staticmethod(
                lambda *_: (free, "wrapper-container:/var/lib/postgresql/data")
            ),
        )
        with psycopg.connect(migrated) as conn:
            install_instance_scope(conn, ["bdnb"], departements)
        _serve_local(mp, zips, asked)
        pipeline = BdnbBatimentParcellePipeline()
        pipeline.pin_millesime(MILLESIME_URL)
        runner = PipelineRunner(
            [ValidationHook(), DependentMatviewHook()], scope_loader=_stored_scope
        )
        started = time.perf_counter()
        result = runner.run(pipeline)
        duration = time.perf_counter() - started
        assert result.success, result.error
        (record,) = sorted((archive / TABLE / "runs").glob("*.json"))
        _ecrire(
            preuves,
            "run.json",
            {
                "run_id": record.stem,
                "duree_s": round(duration, 1),
                "urls_copiees": sorted(asked),
                "zips": {
                    dept: {"fichier": str(path), "octets": path.stat().st_size}
                    for dept, path in zips.items()
                },
                "compteurs": _counts(result),
                "enregistrement_archive": {
                    "chemin": record.relative_to(archive).as_posix(),
                    "sha256": _sha256(record),
                },
                "pg_libre_octets": free,
                "commandes": audit,
            },
        )
        yield Charge(
            dsn=migrated,
            preuves=preuves,
            attendu=attendu,
            zips=zips,
            departements=departements,
            archive=archive,
            record=record,
            run_id=record.stem,
            counters=_counts(result),
        )


@pytest.fixture(scope="module")
def fidelite(charge):
    """``count --archive-run``, ``loaded`` and ``diff``, written as proofs."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", charge.dsn)
        code, producer = _cli(
            [
                "count",
                "--source",
                "bdnb",
                "--archive-run",
                f"{TABLE}:{charge.run_id}",
                "--archive-root",
                str(charge.archive),
            ]
        )
        assert code == 0 and producer["pannes"] == {}, producer.get("pannes")
        code, loaded = _cli(["loaded", "--dataset", TABLE])
        assert code == 0
    p_path = _ecrire(charge.preuves, "fidelite-archive.json", producer)
    l_path = _ecrire(charge.preuves, "fidelite-base.json", loaded)
    code, diff = _cli(["diff", "--producer", str(p_path), "--loaded", str(l_path)])
    _ecrire(charge.preuves, "fidelite-diff.json", diff)
    return {"code": code, "producer": producer, "loaded": loaded, "diff": diff}


# --- Receipt, fidelity, counts ------------------------------------------------


def test_the_receipt_of_the_run_and_its_millesime(charge, fidelite):
    out = charge.preuves / "recu" / f"{charge.run_id}.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", charge.dsn)
        code = eim.main(
            [
                "--dataset",
                TABLE,
                "--run-id",
                charge.run_id,
                "--repo-sha",
                _repo_sha(),
                "--out",
                str(out),
                "--source-archive-dir",
                str(charge.archive),
            ]
        )
    assert code == eim.EXIT_OK
    recu = json.loads(out.read_text("utf-8"))
    stages = {
        name.removeprefix("count:"): value
        for name, value in recu["counting"]["stages_rows_affected"].items()
        if name.startswith("count:")
    }
    assert stages == charge.counters
    # Raw metrics of the archive count = counters of the receipt, exact.
    assert fn.bdnb_receipt_counters(fidelite["producer"]) == stages
    # D13: a non-zero value would already have refused the swap.
    assert stages["ffo_divergents"] == 0
    assert recu["etl_run"]["status"] == "success"
    assert recu["source_archive"]["record"] == (
        charge.record.relative_to(charge.archive).as_posix()
    )
    assert recu["source_archive"]["record_sha256"] == _sha256(charge.record)
    # One millesime: the archive record, the metadata and the receipt.
    contenu = json.loads(charge.record.read_text("utf-8"))
    assert contenu["source_period_label"] == LABEL
    with psycopg.connect(charge.dsn) as conn:
        label, departements = conn.execute(
            "SELECT source_period_label, effective_departments "
            "FROM dataset_metadata WHERE dataset_name = %s",
            (TABLE,),
        ).fetchone()
    assert label == LABEL
    assert sorted(departements) == list(charge.departements)
    assert recu["dataset_metadata"]["source_period_label"] == LABEL


def test_the_fidelity_diff_is_egal_for_every_departement(charge, fidelite):
    diff = fidelite["diff"]
    assert fidelite["code"] == 0, diff.get("erreurs")
    assert set(diff["metriques"]) == set(fn.METRIQUES_COMPAREES["bdnb"])
    for metric, cells in diff["metriques"].items():
        assert set(cells) == set(charge.departements), metric
        assert {cell["classe"] for cell in cells.values()} == {"egal"}, metric
    assert diff["millesime"] == {
        "etiquettes_zips": dict.fromkeys(charge.departements, LABEL),
        "enregistrement": LABEL,
        "base": LABEL,
        "classe": "egal",
    }


def _relations_du_fichier(path: Path) -> tuple[int, int, set[tuple[str, str]]]:
    """Rows, rows with both ids, and distinct (parcel, group) pairs of the
    relations member: an independent read of the file, not the ETL's code."""
    with zipfile.ZipFile(path) as archive, archive.open(MEMBER_RELATIONS) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig"), delimiter=";")
        header = next(reader)
        i_g, i_p = header.index("batiment_groupe_id"), header.index("parcelle_id")
        lues = valides = 0
        paires: set[tuple[str, str]] = set()
        for row in reader:
            if not row:
                continue
            lues += 1
            groupe, parcelle = row[i_g].strip(), row[i_p].strip()
            if groupe and parcelle:
                valides += 1
                paires.add((parcelle, groupe))
    return lues, valides, paires


def _repartition(paires) -> Counter:
    return Counter(Counter(parcelle for parcelle, _ in paires).values())


def test_published_rows_are_the_relations_minus_each_named_gap(charge, fidelite):
    lus = {dept: _relations_du_fichier(path) for dept, path in charge.zips.items()}
    counters = charge.counters
    ecarts: dict[str, dict[str, int]] = {}
    deja: set[tuple[str, str]] = set()
    # A pair of two files is kept in the smallest code (COLLATE "C").
    for dept in sorted(lus):
        lues, valides, paires = lus[dept]
        retirees = len(paires & deja)
        ecarts[dept] = {
            "relations_fichier": lues,
            "relations_ecartees": lues - valides,
            "doublons_intra_fichier": valides - len(paires),
            "doublons_inter_fichiers_retires": retirees,
            "publiees": counters[f"publiees_{dept}"],
        }
        deja |= paires
    _ecrire(charge.preuves, "ecarts.json", ecarts)
    for dept, e in ecarts.items():
        assert counters[f"producteur_relations_{dept}"] == e["relations_fichier"]
        assert counters[f"relations_ecartees_{dept}"] == e["relations_ecartees"]
        assert e["publiees"] == (
            e["relations_fichier"]
            - e["relations_ecartees"]
            - e["doublons_intra_fichier"]
            - e["doublons_inter_fichiers_retires"]
        ), dept
    assert counters["doublons_inter_fichiers"] == sum(
        e["doublons_inter_fichiers_retires"] for e in ecarts.values()
    )
    assert counters["ffo_divergents"] == 0


def test_the_85_counts_are_the_research(charge, fidelite):
    lues, _valides, paires = _relations_du_fichier(charge.zips["85"])
    fichier = _repartition(paires)
    # The base keeps in 85 the pairs no smaller code holds.
    autres = set().union(
        *(
            _relations_du_fichier(path)[2]
            for dept, path in charge.zips.items()
            if dept < "85"
        )
    )
    attendu_base = _repartition(paires - autres)
    with psycopg.connect(charge.dsn) as conn:
        base_85 = Counter(
            dict(
                conn.execute(
                    f"SELECT n, count(*) FROM (SELECT parcelle_id, count(*) AS n "
                    f"FROM {TABLE} WHERE code_departement = '85' "
                    f"GROUP BY parcelle_id) s GROUP BY n"
                ).fetchall()
            )
        )
    metrics_85 = fidelite["producer"]["counts"]["85"]
    mesure = {
        "relations_fichier": lues,
        "ffo_fichier": metrics_85["bdnb:brut:ffo_fichier"],
        "parcelles_distinctes_fichier": sum(fichier.values()),
        "groupes_par_parcelle_fichier": dict(sorted(fichier.items())),
        "groupes_par_parcelle_base": dict(sorted(base_85.items())),
        "recherche": {
            **RECHERCHE_85,
            "au_moins_12": RECHERCHE_85_AU_MOINS_12,
        },
    }
    _ecrire(charge.preuves, "comptes-85.json", mesure)
    assert lues == RECHERCHE_85["relations"]
    assert metrics_85["bdnb:brut:relations_fichier"] == RECHERCHE_85["relations"]
    assert mesure["ffo_fichier"] == RECHERCHE_85["ffo"]
    assert (
        mesure["parcelles_distinctes_fichier"] == RECHERCHE_85["parcelles_distinctes"]
    )
    assert {n: fichier[n] for n in (1, 2, 3)} == RECHERCHE_85["groupes_par_parcelle"]
    assert sum(v for n, v in fichier.items() if n >= 12) == RECHERCHE_85_AU_MOINS_12
    assert sum(v for n, v in fichier.items() if n >= 11) == MESURE_85_AU_MOINS_11
    assert base_85 == attendu_base


# --- Reader of the theme ------------------------------------------------------


def _valider_attendu(doc: Any) -> dict[str, list[dict]]:
    """Shape of the ``attendu`` output of scripts/bdnb_equivalence.py."""
    assert isinstance(doc, dict)
    assert set(doc) == {"regle", "millesime_fichier", "compteurs", "parcelles"}
    assert doc["millesime_fichier"] == LABEL
    assert isinstance(doc["compteurs"], dict)
    assert all(
        isinstance(v, int) and not isinstance(v, bool)
        for v in doc["compteurs"].values()
    )
    parcelles = doc["parcelles"]
    assert isinstance(parcelles, dict) and parcelles
    for parcelle, groupes in parcelles.items():
        assert isinstance(parcelle, str) and len(parcelle) == 14, parcelle
        assert isinstance(groupes, list), parcelle
        ids = [g.get("batiment_groupe_id") for g in groupes]
        assert len(set(ids)) == len(ids), parcelle
        for g in groupes:
            assert set(g) == CHAMPS_ATTENDU, parcelle
            assert isinstance(g["batiment_groupe_id"], str) and g["batiment_groupe_id"]
            annee = g["annee_construction"]
            assert annee is None or (
                isinstance(annee, int) and not isinstance(annee, bool)
            )
            assert g["mat_mur_txt"] is None or isinstance(g["mat_mur_txt"], str)
            assert g["mat_toit_txt"] is None or isinstance(g["mat_toit_txt"], str)
            assert isinstance(g["parcelle_principale"], bool)
    assert {LUCON["parcelle"], DEUX_GROUPES["parcelle"]} <= set(parcelles)
    return parcelles


def _tri(groupe: dict) -> tuple:
    # ORDER BY parcelle_principale DESC, batiment_groupe_id of the reader.
    return (not groupe["parcelle_principale"], groupe["batiment_groupe_id"])


def _sans_principale(groupe: dict) -> dict:
    return {k: v for k, v in groupe.items() if k != "parcelle_principale"}


async def _lire_tout(dsn: str, parcelles: list[str]) -> dict[str, tuple]:
    """The reader of the theme on every parcel, with its duration."""
    engine = create_async_engine(_async_dsn(dsn))
    lus: dict[str, tuple] = {}
    try:
        async with AsyncSession(engine) as session:
            for parcelle in parcelles:
                started = time.perf_counter()
                lu = await construction.lire_import(session, parcelle)
                lus[parcelle] = (lu, time.perf_counter() - started)
    finally:
        await engine.dispose()
    return lus


async def test_the_reader_is_the_attendu_output_on_the_whole_sample(charge):
    if charge.attendu is None:
        pytest.skip(
            "DC_BDNB_ATTENDU absent: the reader = rule part (base = file) "
            "is not checked in this run"
        )
    doc = json.loads(charge.attendu.read_text("utf-8"))
    parcelles = _valider_attendu(doc)
    if set(charge.zips) == {"44", "85"}:
        # The rule was applied to these very zips.
        assert doc["compteurs"]["relations_lues"] == sum(
            charge.counters[f"producteur_relations_{d}"] for d in charge.zips
        )
        assert doc["compteurs"]["ffo_lues"] == sum(
            charge.counters[f"producteur_ffo_{d}"] for d in charge.zips
        )
    with psycopg.connect(charge.dsn) as conn:
        rows = conn.execute(
            f"SELECT parcelle_id, batiment_groupe_id, annee_construction, "
            f"mat_mur_txt, mat_toit_txt, parcelle_principale FROM {TABLE} "
            f"WHERE parcelle_id = ANY(%s)",
            (list(parcelles),),
        ).fetchall()
    en_base: dict[str, list[dict]] = {p: [] for p in parcelles}
    for parcelle, groupe, annee, mur, toit, principale in rows:
        en_base[parcelle].append(
            {
                "batiment_groupe_id": groupe,
                "annee_construction": annee,
                "mat_mur_txt": mur,
                "mat_toit_txt": toit,
                "parcelle_principale": principale,
            }
        )
    lus = await _lire_tout(charge.dsn, sorted(parcelles))
    ecarts: dict[str, dict] = {}
    for parcelle, attendus in parcelles.items():
        ordre = sorted(attendus, key=_tri)
        lu, _duree = lus[parcelle]
        obtenu = {
            "base": sorted(en_base[parcelle], key=_tri),
            "total": lu.total,
            "annees": sorted(lu.annees, key=lambda a: (a is None, a)),
            "lignes": lu.lignes,
        }
        voulu = {
            "base": ordre,
            "total": len(attendus),
            "annees": sorted(
                {g["annee_construction"] for g in attendus},
                key=lambda a: (a is None, a),
            ),
            "lignes": [_sans_principale(g) for g in ordre[: construction.LISTE_MAX]],
        }
        if obtenu != voulu:
            ecarts[parcelle] = {"obtenu": obtenu, "attendu": voulu}
    _ecrire(
        charge.preuves,
        "lecteur-attendu.json",
        {
            "attendu": str(charge.attendu),
            "attendu_sha256": _sha256(charge.attendu),
            "parcelles": len(parcelles),
            "groupes": sum(len(g) for g in parcelles.values()),
            "egales": len(parcelles) - len(ecarts),
            "ecarts": ecarts,
        },
    )
    assert ecarts == {}


# --- Theme, access path, sizes ------------------------------------------------


async def _theme(dsn: str, *, ban_id: str, commune: str, parcelle: str) -> dict:
    """The ``construction`` theme read on the loaded base.

    Called through ``lire_theme`` with a context of the address, not through
    the route: the route resolves the BAN key and the parcel of the point in
    ``adresses`` and ``parcelles``, which this run does not load. The parcel
    is the one the BAN point falls in (``unique``), as the route would give.
    """
    scope = _stored_scope(dsn)
    engine = create_async_engine(_async_dsn(dsn))
    try:
        async with AsyncSession(engine) as session:
            contexte = ContexteRapport(
                session=session,
                identite=IdentiteAdresse(
                    ban_id=ban_id,
                    commune=CommuneRef(code_insee=commune),
                    departement=DepartementRef(code="85", nom="Vendée"),
                    parcelle=RelationParcelle(
                        etat="unique",
                        id_parcelle=parcelle,
                        nombre_candidats=1,
                        rattachement="parcelle_du_point",
                    ),
                ),
                point=None,
                hosting=route_hosting(["bdnb"], scope, "85"),
                scope=scope,
                as_of=date.today(),
                memo=Memo(),
            )
            theme = await lire_theme(construction.THEME, contexte)
    finally:
        await engine.dispose()
    return theme.model_dump(mode="json")


async def test_the_construction_theme_on_two_real_parcels(charge):
    lucon = await _theme(
        charge.dsn,
        ban_id=LUCON["ban_id"],
        commune=LUCON["commune"],
        parcelle=LUCON["parcelle"],
    )
    # The BAN key is not read by the theme: only the parcel and its departement.
    plusieurs = await _theme(
        charge.dsn,
        ban_id=f"{DEUX_GROUPES['commune']}_0000_00000",
        commune=DEUX_GROUPES["commune"],
        parcelle=DEUX_GROUPES["parcelle"],
    )
    _ecrire(
        charge.preuves,
        "theme.json",
        {LUCON["parcelle"]: lucon, DEUX_GROUPES["parcelle"]: plusieurs},
    )
    assert (lucon["etat"], lucon["ton"]) == ("ok", "neutre")
    assert lucon["verdict"] == "Construit en 1984 selon la BDNB."
    assert lucon["rattachement"]["confiance"] == "probable"
    assert lucon["rattachement"]["cle"] == LUCON["groupe"]
    assert [(s["cle"], s["mode"], s["millesime"]) for s in lucon["sources"]] == [
        ("bdnb", "import", LABEL)
    ]
    assert lucon["chiffres"][0]["millesime"] == LABEL
    assert (plusieurs["etat"], plusieurs["motif"]) == (
        "manque",
        "plusieurs_batiments_sur_la_parcelle",
    )
    assert plusieurs["rattachement"]["candidats"] == 2


def _noeuds(plan: dict):
    yield plan
    for child in plan.get("Plans", []):
        yield from _noeuds(child)


def _quantiles(durees: list[float]) -> dict[str, float]:
    ordonnees = sorted(durees)
    p95 = ordonnees[round(0.95 * (len(ordonnees) - 1))]
    return {
        "p50_ms": round(statistics.median(ordonnees) * 1000, 2),
        "p95_ms": round(p95 * 1000, 2),
        "n": len(ordonnees),
    }


def _echantillon(charge) -> tuple[str, list[str]]:
    """The equivalence sample when given, else parcels drawn from the base."""
    if charge.attendu is not None:
        doc = json.loads(charge.attendu.read_text("utf-8"))
        return "attendu", sorted(doc["parcelles"])
    with psycopg.connect(charge.dsn) as conn:
        tirees = [
            row[0]
            for row in conn.execute(
                f"SELECT parcelle_id FROM (SELECT DISTINCT parcelle_id FROM {TABLE}) p "
                "ORDER BY md5(parcelle_id) LIMIT 160"
            )
        ]
    return "base", sorted({*tirees, LUCON["parcelle"], DEUX_GROUPES["parcelle"]})


async def test_the_reader_reads_its_index_and_its_latency(charge):
    sql = construction._GROUPES_DE_LA_PARCELLE.text.replace(":parcelle", "%(parcelle)s")
    with psycopg.connect(charge.dsn) as conn:
        (plus_grande,) = conn.execute(
            f"SELECT parcelle_id FROM {TABLE} GROUP BY parcelle_id "
            "ORDER BY count(*) DESC, parcelle_id LIMIT 1"
        ).fetchone()
        plans = {
            parcelle: conn.execute(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}",
                {"parcelle": parcelle},
            ).fetchone()[0][0]
            for parcelle in (LUCON["parcelle"], DEUX_GROUPES["parcelle"], plus_grande)
        }
    origine, parcelles = _echantillon(charge)
    durees: list[float] = []
    for _ in range(REPETITIONS):
        lus = await _lire_tout(charge.dsn, parcelles)
        durees.extend(duree for _lu, duree in lus.values())
    _ecrire(
        charge.preuves,
        "acces.json",
        {
            "explain": plans,
            "latence_lecteur": {
                "echantillon": origine,
                "parcelles": len(parcelles),
                "repetitions": REPETITIONS,
                **_quantiles(durees),
            },
        },
    )
    for parcelle, plan in plans.items():
        noeuds = list(_noeuds(plan["Plan"]))
        assert any(n.get("Index Name") == INDEX for n in noeuds), parcelle
        assert not any(
            n.get("Node Type") == "Seq Scan" and n.get("Relation Name") == TABLE
            for n in noeuds
        ), parcelle


def test_sizes_and_the_distribution_of_the_years(charge, fidelite):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", charge.dsn)
        # Exact counts: estimation_nationale.py (T4b) reads ``exact_count``.
        argv = ["--out", str(charge.preuves / "tailles.json"), "--exact-counts"]
        assert msz.main(argv) == 0
    tailles = json.loads((charge.preuves / "tailles.json").read_text("utf-8"))
    table = tailles["tables"][TABLE]
    index = {i["name"]: i for i in tailles["indexes"]}[INDEX]
    assert index["table"] == TABLE and index["is_unique"]
    with psycopg.connect(charge.dsn) as conn:
        rows = conn.execute(
            f"SELECT code_departement, count(*), min(annee_construction), "
            f"max(annee_construction), "
            f"count(*) FILTER (WHERE annee_construction IS NULL), "
            f"count(*) FILTER (WHERE annee_construction <= 0 OR "
            f"annee_construction > extract(year FROM current_date)) "
            f"FROM {TABLE} GROUP BY 1 ORDER BY 1"
        ).fetchall()
    annees = {
        dept: {
            "lignes": lignes,
            "min": minimum,
            "max": maximum,
            "null": nulles,
            "implausibles": implausibles,
        }
        for dept, lignes, minimum, maximum, nulles, implausibles in rows
    }
    _ecrire(
        charge.preuves,
        "annees.json",
        {
            "annees": annees,
            "table": {
                "total_octets": table["total_bytes"],
                "table_octets": table["table_bytes"],
                "index_octets": table["indexes_bytes"],
                "toast_octets": table["toast_bytes"],
                "index_unique_octets": index["bytes"],
                "octets_par_ligne": round(
                    table["total_bytes"] / sum(a["lignes"] for a in annees.values()),
                    1,
                ),
            },
        },
    )
    assert sorted(annees) == list(charge.departements)
    assert table["exact_count"] == sum(a["lignes"] for a in annees.values())
    counts = fidelite["loaded"]["counts"]
    for dept, a in annees.items():
        assert a["lignes"] == counts[dept][TABLE]
        assert a["lignes"] - a["null"] == counts[dept]["bdnb:annee_renseignee"]
